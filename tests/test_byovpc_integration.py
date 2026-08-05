"""Integration tests that provision a stand-in customer VPC via the byovpc fixture.

Deselected by default. Run one shape at a time:

    pytest -m integration tests/test_byovpc_integration.py -k carve -s

Stack names are "$USER-<mode>". Profile and region come from pytest ini
(aws_profile / aws_region). Pass --keep-vpc to leave the stack up.
"""

import ipaddress
import os

import pytest
from e2e.aws import ELB_TAG, INTERNAL_ELB_TAG, tags
from e2e.wizard import parse_wizard_env

pytestmark = pytest.mark.integration


def assert_vpc_baseline(ec2, outputs, expected_mode):
    assert outputs["mode"] == expected_mode
    vpc_id = outputs["vpc_id"]
    assert vpc_id.startswith("vpc-")

    described = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]
    assert described["State"] == "available"
    for attribute in ("enableDnsSupport", "enableDnsHostnames"):
        value = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute=attribute)
        key = attribute[0].upper() + attribute[1:]
        assert value[key]["Value"] is True, f"{attribute} must be enabled for EKS"

    main = ec2.describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "association.main", "Values": ["true"]},
        ]
    )["RouteTables"][0]
    default_routes = [
        r
        for r in main["Routes"]
        if r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("State") == "active"
    ]
    assert default_routes, "main route table must carry egress so carved subnets inherit it"
    assert default_routes[0].get("NatGatewayId"), "egress should go via the fixture NAT"


def subnets_by_role(ec2, vpc_id):
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    public = [s for s in subnets if ELB_TAG in tags(s)]
    private = [s for s in subnets if INTERNAL_ELB_TAG in tags(s)]
    return subnets, public, private


def assert_wizard_env(outputs, expected):
    env = parse_wizard_env(outputs["wizard_env"])
    assert env["PINECONE_EXISTING_VPC_ID"] == outputs["vpc_id"]
    assert env["PINECONE_REGION"] == os.environ["AWS_REGION"]
    for key, value in expected.items():
        assert env[key] == value, f"{key}: expected {value!r}, got {env[key]!r}"
    return env


@pytest.mark.parametrize("byovpc", ["carve"], indirect=True)
def test_carve_vpc_has_no_workload_subnets_but_has_egress(ec2, byovpc):
    assert_vpc_baseline(ec2, byovpc, "carve")
    vpc_id = byovpc["vpc_id"]

    _, public, private = subnets_by_role(ec2, vpc_id)
    assert public == [], "carve target must not expose elb-tagged subnets"
    assert private == [], "carve target must have no pre-tagged private subnets"
    assert byovpc["public_subnet_ids"] == ""
    assert byovpc["private_subnet_ids"] == ""

    env = assert_wizard_env(
        byovpc,
        {
            "PINECONE_PUBLIC_ACCESS": "true",
            "PINECONE_PRIVATE_SUBNET_IDS": "",
        },
    )

    carve = ipaddress.IPv4Network(env["PINECONE_VPC_CIDR"])
    assert carve.prefixlen == 16, "the module requires a /16 to carve from"
    associated = [
        ipaddress.IPv4Network(a["CidrBlock"])
        for a in ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]["CidrBlockAssociationSet"]
    ]
    assert not any(carve.overlaps(net) for net in associated), (
        f"carve range {carve} must be disjoint from the VPC so the module can associate it"
    )
