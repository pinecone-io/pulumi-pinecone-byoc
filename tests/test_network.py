"""Targeted applies: create the network the module owns, and nothing after it.

`pulumi up --target`, limited to the VPC component and its children, is the
cheap half of the e2e story. The subnets are real, so a carve CIDR that clashes
with the customer's, a bad AZ or a rejected CIDR association fails here exactly
as it would in a full deploy - but the run stops before EKS, RDS and the control
plane, which is where the hour goes. It needs no PINECONE_API_KEY and no egress
anywhere, so the customer VPC is the bare one from the customer_vpc fixture.

Both shapes run the same apply and the same assertions; they differ only in what
the module is expected to create, which is a table.

    pytest -m network tests/test_network.py -k carve -s

Interrupted runs leave the project in .e2e/<stack>/, so teardown is
`cd .e2e/$USER-<shape>-network && pulumi destroy --yes` followed by deleting the
stand-in VPC (--keep-vpc prints the command).
"""

import ipaddress
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from e2e.commands import pulumi, pulumi_json, run
from e2e.paths import PROJECTS, REPO_ROOT
from e2e.plan import network_targets
from e2e.settings import e2e_azs, keep_stacks
from e2e.stacks import destroy_stack, stack_name

pytestmark = pytest.mark.network

UNUSED_API_KEY = "the-network-tier-never-calls-the-control-plane"
SUBNET = "aws:ec2/subnet:Subnet"
VPC = "aws:ec2/vpc:Vpc"
NAT = "aws:ec2/natGateway:NatGateway"
CIDR_ASSOCIATION = "aws:ec2/vpcIpv4CidrBlockAssociation:VpcIpv4CidrBlockAssociation"


class Applied(NamedTuple):
    project_dir: Path
    shape: str
    azs: list[str]
    vpc_id: str
    cidr: str


def expected_counts(shape, az_count):
    if shape == "carve":
        return {VPC: 0, CIDR_ASSOCIATION: 1, SUBNET: az_count, NAT: 0}
    return {VPC: 1, CIDR_ASSOCIATION: 0, SUBNET: 2 * az_count, NAT: az_count}


def generate_project(stack, env):
    project_dir = PROJECTS / stack
    project_dir.mkdir(parents=True, exist_ok=True)
    run(
        sys.executable,
        "setup/wizard.py",
        "--cloud",
        "aws",
        "--headless",
        "--stack-name",
        stack,
        "--output-dir",
        str(project_dir),
        "--dev",
        str(REPO_ROOT),
        cwd=REPO_ROOT,
        env={
            "PINECONE_API_KEY": os.environ.get("PINECONE_API_KEY") or UNUSED_API_KEY,
            "PINECONE_PROJECT_NAME": stack,
            "PINECONE_DELETION_PROTECTION": "false",
            **env,
        },
    )
    return project_dir


def apply_network(project_dir):
    targets = network_targets(pulumi_json("preview", "--json", cwd=project_dir))
    assert targets, "the preview plan held no VPC resources to target"
    logging.info(f"targeting {len(targets)} resource(s):\n" + "\n".join(targets))
    flags = [flag for urn in targets for flag in ("--target", urn)]
    pulumi("up", "--yes", "--skip-preview", *flags, cwd=project_dir)


def created_resources(project_dir):
    state = pulumi_json("stack", "export", cwd=project_dir)
    return state.get("deployment", {}).get("resources", [])


def created_types(resources):
    return Counter(resource["type"] for resource in resources)


def created_vpc_id(resources):
    return next(resource["id"] for resource in resources if resource["type"] == VPC)


@pytest.fixture
def network_project(request):
    shape = request.param
    stack = stack_name(shape, "network")
    cidr = "10.0.0.0/16"
    azs = e2e_azs(request.config)
    customer_vpc_id = None
    env = {
        "PINECONE_REGION": os.environ["AWS_REGION"],
        "PINECONE_AZS": azs,
        "PINECONE_VPC_CIDR": cidr,
        "PINECONE_PUBLIC_ACCESS": "true",
    }
    if shape == "carve":
        customer = request.getfixturevalue("customer_vpc")
        azs = customer["azs"]
        cidr = customer["carve_cidr"]
        customer_vpc_id = customer["vpc_id"]
        env |= {
            "PINECONE_AZS": azs,
            "PINECONE_EXISTING_VPC_ID": customer_vpc_id,
            "PINECONE_VPC_CIDR": cidr,
            "PINECONE_PUBLIC_ACCESS": "false",
        }

    project_dir = generate_project(stack, env)
    try:
        apply_network(project_dir)
        yield Applied(
            project_dir=project_dir,
            shape=shape,
            azs=[az.strip() for az in azs.split(",")],
            vpc_id=customer_vpc_id or created_vpc_id(created_resources(project_dir)),
            cidr=cidr,
        )
    finally:
        if keep_stacks(request):
            message = (
                f"leaving network stack {stack} up - destroy it with: "
                f"cd {project_dir} && pulumi destroy --yes"
            )
            print(f"\n{message}")
            logging.info(message)
        else:
            destroy_stack(project_dir)


def assert_stopped_at_the_network(created):
    for prefix in ("aws:eks", "aws:rds", "kubernetes:", "pinecone:byoc:DatadogApiKey"):
        stray = [resource for resource in created if resource.startswith(prefix)]
        assert not stray, f"targeting should have stopped before {prefix}, got {stray}"


@pytest.mark.parametrize("network_project", ["vanilla", "carve"], indirect=True)
def test_network(network_project, ec2):
    applied = network_project
    azs = applied.azs
    expected = expected_counts(applied.shape, len(azs))

    created = created_types(created_resources(applied.project_dir))
    assert {resource: created[resource] for resource in expected} == expected
    assert_stopped_at_the_network(created)

    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [applied.vpc_id]}])[
        "Subnets"
    ]
    per_az = expected[SUBNET] // len(azs)
    assert Counter(subnet["AvailabilityZone"] for subnet in subnets) == dict.fromkeys(azs, per_az)

    network = ipaddress.ip_network(applied.cidr)
    for subnet in subnets:
        assert ipaddress.ip_network(subnet["CidrBlock"]).subnet_of(network), (
            f"{subnet['SubnetId']} {subnet['CidrBlock']} is outside {network}"
        )
