"""Integration tests that provision a stand-in customer VPC via the byovpc fixture.

Deselected by default. Run one shape at a time:

    pytest -m integration tests/test_byovpc_integration.py -k public -s
    pytest -m integration tests/test_byovpc_integration.py -k private -s
    pytest -m integration tests/test_byovpc_integration.py -k carve -s

Stack names are "$USER-<mode>". Profile and region come from pytest ini
(aws_profile / aws_region). Pass --keep-vpc to leave the stack up.
"""

import ipaddress
import os

import pytest
from byovpc_util import ELB_TAG, INTERNAL_ELB_TAG, parse_wizard_env, tags

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


@pytest.mark.parametrize("byovpc", ["public"], indirect=True)
def test_public_vpc_is_adoptable_with_both_subnet_roles(ec2, byovpc):
    assert_vpc_baseline(ec2, byovpc, "public")
    vpc_id = byovpc["vpc_id"]
    azs = [az.strip() for az in byovpc["azs"].split(",")]

    _, public, private = subnets_by_role(ec2, vpc_id)
    assert {s["AvailabilityZone"] for s in public} == set(azs)
    assert {s["AvailabilityZone"] for s in private} == set(azs)

    exported_public = byovpc["public_subnet_ids"].split(",")
    exported_private = byovpc["private_subnet_ids"].split(",")
    assert set(exported_public) == {s["SubnetId"] for s in public}
    assert set(exported_private) == {s["SubnetId"] for s in private}

    for subnet in public:
        route_table = ec2.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet["SubnetId"]]}]
        )["RouteTables"]
        assert route_table, f"{subnet['SubnetId']} needs an explicit route table"
        igw_routes = [
            r
            for r in route_table[0]["Routes"]
            if r.get("DestinationCidrBlock") == "0.0.0.0/0"
            and str(r.get("GatewayId", "")).startswith("igw-")
        ]
        assert igw_routes, f"{subnet['SubnetId']} is elb-tagged but has no IGW route"

    assert_wizard_env(byovpc, {"PINECONE_PUBLIC_ACCESS": "true"})


@pytest.mark.parametrize("byovpc", ["private"], indirect=True)
def test_private_vpc_exposes_only_internal_subnets(ec2, byovpc):
    assert_vpc_baseline(ec2, byovpc, "private")
    vpc_id = byovpc["vpc_id"]
    azs = [az.strip() for az in byovpc["azs"].split(",")]

    _, public, private = subnets_by_role(ec2, vpc_id)
    assert public == [], "private shape must not expose elb-tagged subnets"
    assert {s["AvailabilityZone"] for s in private} == set(azs)
    assert byovpc["public_subnet_ids"] == ""
    assert set(byovpc["private_subnet_ids"].split(",")) == {s["SubnetId"] for s in private}

    assert_wizard_env(
        byovpc,
        {
            "PINECONE_PUBLIC_ACCESS": "false",
            "PINECONE_PUBLIC_SUBNET_IDS": "",
        },
    )


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
            "PINECONE_PUBLIC_ACCESS": "false",
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
