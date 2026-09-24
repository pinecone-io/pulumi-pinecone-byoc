"""Deploy the module's network on its own, assert on it, and destroy it.

Twice: once where the module builds the VPC, and once into the VPC the first run
built, which by then is a VPC the module did not create - with subnets, per-AZ
route tables and a NAT behind them, which is what a customer's looks like. The
subnets are real, so a rejected CIDR, a bad AZ or a layout that does not fit fails
here exactly as it would in a full deploy, in minutes rather than an hour.

    pytest -m network tests/vpc/test_vpc_live.py -s
"""

import ipaddress
import logging
import os
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import boto3
import pytest
from e2e.commands import pulumi, pulumi_json
from e2e.settings import e2e_azs, keep_stacks
from e2e.stacks import destroy_stack, stack_name

pytestmark = [pytest.mark.cloud, pytest.mark.network]

PROGRAM_DIR = Path(__file__).resolve().parent / "program"

VPC = "aws:ec2/vpc:Vpc"
SUBNET = "aws:ec2/subnet:Subnet"
NAT = "aws:ec2/natGateway:NatGateway"
CIDR_ASSOCIATION = "aws:ec2/vpcIpv4CidrBlockAssociation:VpcIpv4CidrBlockAssociation"
ROUTE_TABLE = "aws:ec2/routeTable:RouteTable"

CONTROL_PLANE_RESOURCES = (
    "pc-environment",
    "pc-cpgw-api-key",
    "pc-service-account",
    "pc-api-key",
    "pc-datadog-api-key",
)


def expected(shape, az_count):
    """What the module owns in each shape.

    In a customer's VPC it owns neither the VPC nor the egress, and with no public
    access it owns no route table either - nothing of ours points at their gateway.
    """
    if shape == "private":
        return {VPC: 0, CIDR_ASSOCIATION: 1, SUBNET: az_count, NAT: 0, ROUTE_TABLE: 0}
    if shape == "existing":
        return {VPC: 0, CIDR_ASSOCIATION: 1, SUBNET: 2 * az_count, NAT: 0, ROUTE_TABLE: 1}
    return {
        VPC: 1,
        CIDR_ASSOCIATION: 0,
        SUBNET: 2 * az_count,
        NAT: az_count,
        ROUTE_TABLE: 1 + az_count,
    }


class Applied(NamedTuple):
    stack: str
    azs: list[str]
    shape: str
    vpc_id: str
    cidr: str


@pytest.fixture(scope="module")
def ec2():
    return boto3.client("ec2", region_name=os.environ["AWS_REGION"])


def deploy(request, shape, cidr, existing_vpc_id=None, public_access=True):
    stack = stack_name("network", shape)
    azs = [az.strip() for az in e2e_azs(request.config).split(",")]

    pulumi("stack", "select", "--create", stack, cwd=PROGRAM_DIR)
    pulumi("config", "set", "aws:region", os.environ["AWS_REGION"], cwd=PROGRAM_DIR)
    pulumi("config", "set", "region", os.environ["AWS_REGION"], cwd=PROGRAM_DIR)
    pulumi("config", "set", "vpc-cidr", cidr, cwd=PROGRAM_DIR)
    for index, az in enumerate(azs):
        pulumi("config", "set", "--path", f"availability-zones[{index}]", az, cwd=PROGRAM_DIR)
    if existing_vpc_id:
        pulumi("config", "set", "existing-vpc-id", existing_vpc_id, cwd=PROGRAM_DIR)
    pulumi(
        "config",
        "set",
        "public-access-enabled",
        "true" if public_access else "false",
        cwd=PROGRAM_DIR,
    )

    try:
        pulumi("up", "--yes", "--skip-preview", "--stack", stack, cwd=PROGRAM_DIR)
        outputs = pulumi_json("stack", "output", "--json", "--stack", stack, cwd=PROGRAM_DIR)
        yield Applied(
            stack=stack,
            azs=azs,
            shape=shape,
            vpc_id=existing_vpc_id or outputs["vpc_id"],
            cidr=cidr,
        )
    finally:
        if keep_stacks(request):
            logging.info(
                "leaving %s up - destroy it with: cd %s && pulumi destroy --yes --stack %s",
                stack,
                PROGRAM_DIR,
                stack,
            )
        else:
            destroy_stack(PROGRAM_DIR, stack)


@pytest.fixture(scope="module")
def our_vpc(request):
    yield from deploy(request, "module", cidr="10.0.0.0/16")


@pytest.fixture(scope="module")
def their_vpc_private(request, our_vpc):
    """The same adoption with no public access: no gateway of theirs is asked for."""
    yield from deploy(
        request,
        "private",
        cidr="10.2.0.0/16",
        existing_vpc_id=our_vpc.vpc_id,
        public_access=False,
    )


@pytest.fixture(scope="module")
def their_vpc(request, our_vpc):
    """The network the first run built is the VPC this one is given.

    It has their subnets, their route tables and their NAT by then, so the module
    has something to detect rather than a bare range - and is torn down first,
    because its subnets are inside the other one's VPC.
    """
    yield from deploy(request, "existing", cidr="10.1.0.0/16", existing_vpc_id=our_vpc.vpc_id)


def resources(applied):
    state = pulumi_json("stack", "export", "--stack", applied.stack, cwd=PROGRAM_DIR)
    return state.get("deployment", {}).get("resources", [])


@pytest.mark.parametrize("network", ["our_vpc", "their_vpc", "their_vpc_private"])
def test_the_module_builds_its_network_and_stops(network, request):
    applied = request.getfixturevalue(network)
    counts = expected(applied.shape, len(applied.azs))
    created = resources(applied)
    types = Counter(resource["type"] for resource in created)

    assert {resource: types[resource] for resource in counts} == counts

    for prefix in ("aws:eks", "aws:rds", "kubernetes:"):
        stray = [r["type"] for r in created if r["type"].startswith(prefix)]
        assert not stray, f"a network-only deploy must stop before {prefix}, got {stray}"

    urns = [resource["urn"] for resource in created]
    for name in CONTROL_PLANE_RESOURCES:
        assert not [urn for urn in urns if urn.endswith(f"::{name}")], (
            f"{name} registers with the control plane, and the network needs no identity"
        )


@pytest.mark.parametrize("network", ["our_vpc", "their_vpc", "their_vpc_private"])
def test_the_subnets_are_real_and_inside_the_range_we_own(network, request, ec2):
    applied = request.getfixturevalue(network)
    ours = ipaddress.ip_network(applied.cidr)

    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [applied.vpc_id]}])[
        "Subnets"
    ]
    ours_only = [s for s in subnets if ipaddress.ip_network(s["CidrBlock"]).subnet_of(ours)]

    per_az = expected(applied.shape, len(applied.azs))[SUBNET] // len(applied.azs)
    found = Counter(subnet["AvailabilityZone"] for subnet in ours_only)
    assert found == dict.fromkeys(applied.azs, per_az), (
        f"expected {per_az} subnet(s) in each of {applied.azs}, got {dict(found)}"
    )


@pytest.mark.parametrize("network", ["our_vpc", "their_vpc", "their_vpc_private"])
def test_every_private_subnet_of_ours_egresses_through_a_nat(network, request, ec2):
    """The module builds the NAT in its own VPC and finds theirs in one it adopts.

    Either way a node in that subnet has to reach the registry, so the assertion is
    the same: the table serving it leaves by a NAT rather than nowhere.
    """
    applied = request.getfixturevalue(network)
    ours = ipaddress.ip_network(applied.cidr)

    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [applied.vpc_id]}])[
        "Subnets"
    ]
    private = [
        s
        for s in subnets
        if ipaddress.ip_network(s["CidrBlock"]).subnet_of(ours)
        and s["Tags"]
        and any(t["Key"] == "kubernetes.io/role/internal-elb" for t in s["Tags"])
    ]
    assert private, "no private subnet of ours to check"

    for subnet in private:
        tables = ec2.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet["SubnetId"]]}]
        )["RouteTables"]
        assert tables, (
            f"{subnet['SubnetId']} is on the VPC main route table, which is only right "
            "when the customer egresses there"
        )
        default = [r for r in tables[0]["Routes"] if r.get("DestinationCidrBlock") == "0.0.0.0/0"]
        assert default and default[0].get("NatGatewayId"), (
            f"{subnet['SubnetId']} has no NAT egress: {default}"
        )
