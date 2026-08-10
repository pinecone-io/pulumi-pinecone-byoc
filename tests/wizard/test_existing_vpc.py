"""What the wizard asks when a VPC already exists, and what it writes down.

A key the wizard emits and the program never reads, or the reverse, is invisible
until an hour of deploy ends with the module building a VPC of its own.
"""

import ipaddress

import pytest
from wizard import AWSPreflightChecker, AWSSetupWizard


def checker(**kwargs):
    made = object.__new__(AWSPreflightChecker)
    made.results = []
    made.region = "us-east-2"
    made.azs = ["us-east-2a", "us-east-2b"]
    made.cidr = kwargs.get("cidr", "10.1.0.0/16")
    made.vpc_id = kwargs.get("vpc_id", "vpc-theirs")
    made.route_table_ids = kwargs.get("route_table_ids")
    made.public_access = kwargs.get("public_access", True)
    made.non_interactive = True
    return made


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("us-east-2a=rtb-1,us-east-2b=rtb-2", {"us-east-2a": "rtb-1", "us-east-2b": "rtb-2"}),
        ("us-east-2a=rtb-1, us-east-2b=rtb-2", {"us-east-2a": "rtb-1", "us-east-2b": "rtb-2"}),
        ("", None),
    ],
)
def test_route_tables_are_read_as_az_to_table(value, expected):
    assert AWSSetupWizard._parse_route_table_ids(value) == expected


@pytest.mark.parametrize("value", ["us-east-2a=nope", "=rtb-1", "rtb-1"])
def test_a_route_table_that_is_not_an_az_and_a_table_is_refused(value):
    with pytest.raises(ValueError, match="PINECONE_ROUTE_TABLE_IDS"):
        AWSSetupWizard._parse_route_table_ids(value)


def test_the_suggested_range_avoids_what_their_vpc_already_carries():
    suggested = ipaddress.ip_network(AWSSetupWizard._suggest_cidr(["10.0.0.0/16", "10.1.0.0/16"]))

    for theirs in ("10.0.0.0/16", "10.1.0.0/16"):
        assert not suggested.overlaps(ipaddress.ip_network(theirs)), (
            "a range they already carry is where their own subnets live"
        )
    assert suggested.prefixlen == 16


def test_the_suggestion_stays_in_the_block_they_are_already_using():
    suggested = ipaddress.ip_network(AWSSetupWizard._suggest_cidr(["172.16.0.0/16"]))

    assert suggested.subnet_of(ipaddress.ip_network("172.16.0.0/12")), (
        "keeping to the block they numbered from is what makes it routable for them"
    )


def test_a_range_inside_theirs_needs_no_association_and_passes():
    check = checker(cidr="10.0.1.0/24")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert check.results[-1].passed
    assert "inside their" in check.results[-1].message


def test_a_range_half_overlapping_theirs_is_refused():
    check = checker(cidr="10.0.0.0/15")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "overlaps" in check.results[-1].message


def test_egress_is_read_from_the_tables_named_for_our_subnets():
    """Not the main table, which our subnets need never touch."""
    check = checker(route_table_ids={"us-east-2a": "rtb-a", "us-east-2b": "rtb-b"})
    check.ec2 = _ec2(
        route_tables={
            "rtb-a": {"NatGatewayId": "nat-a"},
            "rtb-b": {"TransitGatewayId": "tgw-b"},
            "rtb-main": {"GatewayId": "igw-theirs"},
        }
    )

    check._check_their_egress()

    assert check.results[-1].passed
    assert "rtb-main" not in check.results[-1].message
    assert "nat-a" in check.results[-1].message


def test_a_vpc_that_only_egresses_by_gateway_is_reported_with_the_alternatives():
    check = checker()
    check.ec2 = _ec2(route_tables={"rtb-main": {"GatewayId": "igw-theirs"}})

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "proxy" in check.results[-1].details


def test_public_access_into_a_vpc_with_no_gateway_is_caught_before_deploying():
    check = checker(public_access=True)
    check.ec2 = _ec2(internet_gateways=[])

    check._check_igw_attached()

    assert not check.results[-1].passed
    assert "PrivateLink" in check.results[-1].details


def _ec2(cidr_blocks=None, route_tables=None, internet_gateways=None):
    class Fake:
        def describe_vpcs(self, VpcIds=None):
            return {
                "Vpcs": [
                    {
                        "VpcId": "vpc-theirs",
                        "CidrBlockAssociationSet": [
                            {"CidrBlock": c, "CidrBlockState": {"State": "associated"}}
                            for c in (cidr_blocks or [])
                        ],
                    }
                ]
            }

        def describe_route_tables(self, RouteTableIds=None, Filters=None):
            wanted = RouteTableIds or list(route_tables or {})
            return {
                "RouteTables": [
                    {
                        "RouteTableId": table_id,
                        "Routes": [
                            {
                                "DestinationCidrBlock": "0.0.0.0/0",
                                "State": "active",
                                **(route_tables or {})[table_id],
                            }
                        ],
                    }
                    for table_id in wanted
                ]
            }

        def describe_internet_gateways(self, Filters=None):
            return {"InternetGateways": internet_gateways or []}

    return Fake()


def test_peering_is_not_egress_however_it_looks():
    """AWS routes nothing transitively over a peering connection, so the peer's NAT
    and gateway cannot be reached and the packets are dropped. The module's own
    detection omits it; this check has to agree, or preflight passes a VPC whose
    nodes then cannot pull an image."""
    check = checker()
    check.ec2 = _ec2(route_tables={"rtb-peered": {"VpcPeeringConnectionId": "pcx-theirs"}})

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "pcx-theirs" not in check.results[-1].message


def test_a_virtual_private_gateway_is_egress():
    check = checker()
    check.ec2 = _ec2(route_tables={"rtb-onprem": {"GatewayId": "vgw-theirs"}})

    check._check_their_egress()

    assert check.results[-1].passed
    assert "vgw-theirs" in check.results[-1].message


def test_one_named_table_without_egress_fails_even_when_another_has_it():
    """Each named table serves an availability zone the nodes run in."""
    check = checker(route_table_ids={"us-east-2a": "rtb-a", "us-east-2b": "rtb-b"})
    check.ec2 = _ec2(
        route_tables={"rtb-a": {"NatGatewayId": "nat-a"}, "rtb-b": {"GatewayId": "igw-theirs"}}
    )

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "rtb-b" in check.results[-1].message
    assert "availability zone" in check.results[-1].details
