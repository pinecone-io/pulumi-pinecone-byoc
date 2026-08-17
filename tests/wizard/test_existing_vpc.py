"""What the wizard asks when a VPC already exists, and what it writes down.

A key the wizard emits and the program never reads, or the reverse, is invisible
until an hour of deploy ends with the module building a VPC of its own.
"""

import ipaddress

import pytest
from wizard import MANAGED_BY, AWSPreflightChecker, AWSSetupWizard, subnet_cidr

from pulumi_pinecone_byoc.aws import vpc_subnet


def checker(**kwargs):
    made = object.__new__(AWSPreflightChecker)
    made.results = []
    made.region = "us-east-2"
    made.azs = kwargs.get("azs", ["us-east-2a", "us-east-2b"])
    made.cidr = kwargs.get("cidr", "10.1.0.0/16")
    made.vpc_id = kwargs.get("vpc_id", "vpc-theirs")
    made.route_table_ids = kwargs.get("route_table_ids")
    made.public_access = kwargs.get("public_access", True)
    made.tags = kwargs.get("tags", {})
    made.non_interactive = True
    if "ec2" in kwargs:
        made.ec2 = kwargs["ec2"]
    return made


class DryRuns:
    """An ec2 client that answers a dry run the way EC2 does, without a credential."""

    def __init__(self, answers):
        self.answers = answers
        self.asked: list[tuple[str, dict]] = []

    def __getattr__(self, operation):
        def call(**kwargs):
            self.asked.append((operation, kwargs))
            raise ClientError({"Error": {"Code": self.answers.get(operation, "DryRunOperation")}})

        return call


class DryRunsReading(DryRuns):
    """Dry runs for the writes, real answers for the lookups they are built from."""

    def __init__(self, answers, reader):
        super().__init__(answers)
        self.reader = reader

    def __getattr__(self, operation):
        if operation.startswith("describe_"):
            return getattr(self.reader, operation)
        return super().__getattr__(operation)


class ClientError(Exception):
    def __init__(self, response):
        super().__init__(response["Error"]["Code"])
        self.response = response


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
    offered = AWSSetupWizard._suggest_cidr(["10.0.0.0/16", "10.1.0.0/16"])
    assert offered
    suggested = ipaddress.ip_network(offered)

    for theirs in ("10.0.0.0/16", "10.1.0.0/16"):
        assert not suggested.overlaps(ipaddress.ip_network(theirs)), (
            "a range they already carry is where their own subnets live"
        )
    assert suggested.prefixlen == 16


def test_the_suggestion_stays_in_the_block_they_are_already_using():
    offered = AWSSetupWizard._suggest_cidr(["172.16.0.0/16"])
    assert offered
    suggested = ipaddress.ip_network(offered)

    assert suggested.subnet_of(ipaddress.ip_network("172.16.0.0/12")), (
        "AWS refuses a secondary CIDR outside the block the VPC numbers from"
    )


def test_nothing_is_suggested_when_their_block_is_full():
    """A /16 VPC in 192.168.0.0/16 leaves no free /16 there, and another block cannot
    be associated - so there is nothing to offer rather than something that fails."""
    assert AWSSetupWizard._suggest_cidr(["192.168.0.0/16"]) is None


def test_a_range_inside_theirs_needs_no_association_and_passes():
    check = checker(cidr="10.0.16.0/20")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert check.results[-1].passed
    assert "inside their" in check.results[-1].message


def a_subnet(subnet_id, cidr, ours=False):
    key, value = MANAGED_BY
    return {
        "SubnetId": subnet_id,
        "CidrBlock": cidr,
        "VpcId": "vpc-theirs",
        "Tags": [{"Key": key, "Value": value}] if ours else [],
    }


def test_a_range_inside_theirs_is_still_refused_when_their_subnets_are_in_it():
    """Carried is not free: their subnets are cut from the same addresses."""
    check = checker(cidr="10.0.16.0/20")
    check.ec2 = _ec2(
        cidr_blocks=["10.0.0.0/16"],
        subnets=[a_subnet("subnet-theirs", "10.0.16.0/24")],
    )

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "10.0.16.0/24 (subnet-theirs)" in check.results[-1].message


@pytest.mark.parametrize("vpc_cidr", ["10.0.0.0/16", "10.1.0.0/18", "192.168.16.0/20"])
@pytest.mark.parametrize("is_public", [True, False], ids=["public", "private"])
def test_the_wizard_lays_the_range_out_where_the_module_does(vpc_cidr, is_public):
    """Two copies of the arithmetic, because bootstrap.sh ships wizard.py alone."""
    assert [subnet_cidr(vpc_cidr, index, is_public) for index in range(3)] == [
        vpc_subnet.cidr(vpc_cidr, index, is_public) for index in range(3)
    ]


def test_a_private_deploy_is_not_refused_by_what_is_in_a_public_slot():
    """The module cuts no public subnet without public access, so neither does this."""
    check = checker(cidr="10.0.16.0/20", public_access=False)
    check.ec2 = _ec2(
        cidr_blocks=["10.0.0.0/16"],
        subnets=[a_subnet("subnet-theirs", "10.0.16.0/26")],
    )

    check._check_range_fits_their_vpc()

    assert check.results[-1].passed

    public = checker(cidr="10.0.16.0/20", public_access=True)
    public.ec2 = check.ec2

    public._check_range_fits_their_vpc()

    assert not public.results[-1].passed


def test_more_zones_than_the_layout_fits_is_a_failed_check_not_a_crash():
    """Four zones read past the sixteenth slot; the module refuses, so this must too."""
    check = checker(
        cidr="10.0.16.0/20", azs=["us-east-2a", "us-east-2b", "us-east-2c", "us-east-1a"]
    )
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "availability zones" in check.results[-1].message


def test_a_slot_the_layout_leaves_spare_is_theirs_to_use():
    """Slot 3 of sixteen is cut by neither shape, so a subnet there is not a clash."""
    check = checker(cidr="10.0.16.0/20")
    check.ec2 = _ec2(
        cidr_blocks=["10.0.0.0/16"],
        subnets=[a_subnet("subnet-theirs", "10.0.19.0/24")],
    )

    check._check_range_fits_their_vpc()

    assert check.results[-1].passed


def test_the_subnets_an_earlier_run_left_are_not_read_as_theirs():
    check = checker(cidr="10.0.16.0/20")
    check.ec2 = _ec2(
        cidr_blocks=["10.0.0.0/16"],
        subnets=[a_subnet("subnet-ours", "10.0.20.0/22", ours=True)],
    )

    check._check_range_fits_their_vpc()

    assert check.results[-1].passed


def test_a_range_half_overlapping_theirs_is_refused():
    check = checker(cidr="10.0.0.0/16")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/20"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "overlaps" in check.results[-1].message


def test_egress_is_read_from_the_tables_named_for_our_subnets():
    """Not the main table, which our subnets need never touch."""
    check = checker(route_table_ids={"us-east-2a": "rtb-a", "us-east-2b": "rtb-b"})
    check.ec2 = _ec2(
        tables={
            "rtb-a": {"NatGatewayId": "nat-a"},
            "rtb-b": {"TransitGatewayId": "tgw-b"},
            "rtb-main": {"GatewayId": "igw-theirs", "main": True},
        }
    )

    check._check_their_egress()

    assert check.results[-1].passed
    assert "rtb-main" not in check.results[-1].message
    assert "nat-a" in check.results[-1].message


def test_one_named_table_without_egress_fails_even_when_another_has_it():
    check = checker(route_table_ids={"us-east-2a": "rtb-a", "us-east-2b": "rtb-b"})
    check.ec2 = _ec2(
        tables={"rtb-a": {"NatGatewayId": "nat-a"}, "rtb-b": {"GatewayId": "igw-theirs"}}
    )

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "us-east-2b" in check.results[-1].message


def test_detection_asks_each_zone_not_whichever_table_leaves():
    """A zone whose table cannot leave is a zone whose nodes cannot pull an image,
    however well the other zone is served."""
    check = checker()
    check.ec2 = _ec2(
        subnets_by_az={"us-east-2a": ["subnet-a"], "us-east-2b": ["subnet-b"]},
        tables={
            "rtb-a": {"NatGatewayId": "nat-a", "subnets": ["subnet-a"]},
            "rtb-b": {"GatewayId": "igw-theirs", "subnets": ["subnet-b"]},
        },
    )

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "us-east-2b" in check.results[-1].message
    assert "us-east-2a" not in check.results[-1].message


def test_two_tables_egressing_in_one_zone_asks_to_be_told_which():
    """Detection refuses to choose, so passing here would green-light a failed deploy."""
    check = checker()
    check.ec2 = _ec2(
        subnets_by_az={"us-east-2a": ["subnet-a"], "us-east-2b": ["subnet-b"]},
        tables={
            "rtb-a1": {"NatGatewayId": "nat-a1", "subnets": ["subnet-a"]},
            "rtb-a2": {"TransitGatewayId": "tgw-a2", "subnets": ["subnet-a"]},
            "rtb-b": {"NatGatewayId": "nat-b", "subnets": ["subnet-b"]},
        },
    )

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "rtb-a1" in check.results[-1].message
    assert "rtb-a2" in check.results[-1].message
    assert "PINECONE_ROUTE_TABLE_IDS" in check.results[-1].details


def test_a_zone_left_out_of_a_partial_map_is_still_checked():
    """The module detects what the map does not name, so preflight has to look."""
    check = checker(route_table_ids={"us-east-2a": "rtb-a"})
    check.ec2 = _ec2(
        subnets_by_az={"us-east-2b": ["subnet-b"]},
        tables={
            "rtb-a": {"NatGatewayId": "nat-a"},
            "rtb-b": {"GatewayId": "igw-theirs", "subnets": ["subnet-b"]},
        },
    )

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "us-east-2b" in check.results[-1].message
    assert "us-east-2a" not in check.results[-1].message


def test_a_zone_left_out_of_a_partial_map_may_inherit_the_main_table():
    check = checker(route_table_ids={"us-east-2a": "rtb-a"})
    check.ec2 = _ec2(
        subnets_by_az={},
        tables={
            "rtb-a": {"NatGatewayId": "nat-a"},
            "rtb-main": {"NatGatewayId": "nat-main", "main": True},
        },
    )

    check._check_their_egress()

    assert check.results[-1].passed
    assert "nat-main" in check.results[-1].message


def test_detection_passes_when_every_zone_leaves():
    check = checker()
    check.ec2 = _ec2(
        subnets_by_az={"us-east-2a": ["subnet-a"], "us-east-2b": ["subnet-b"]},
        tables={
            "rtb-a": {"NatGatewayId": "nat-a", "subnets": ["subnet-a"]},
            "rtb-b": {"TransitGatewayId": "tgw-b", "subnets": ["subnet-b"]},
        },
    )

    check._check_their_egress()

    assert check.results[-1].passed


def test_a_main_table_lookup_that_fails_is_a_failed_check_not_a_crash(monkeypatch):
    check = checker()
    check.ec2 = _ec2(subnets_by_az={}, tables={})

    def throttled():
        raise Exception("RequestLimitExceeded: Request limit exceeded")

    monkeypatch.setattr(check, "_main_table", throttled)

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "Failed to check" in check.results[-1].message


def test_a_zone_with_no_subnets_of_theirs_falls_back_to_the_main_table():
    check = checker()
    check.ec2 = _ec2(
        subnets_by_az={},
        tables={"rtb-main": {"NatGatewayId": "nat-main", "main": True}},
    )

    check._check_their_egress()

    assert check.results[-1].passed
    assert "nat-main" in check.results[-1].message


def test_a_vpc_whose_own_subnets_are_public_egresses_through_the_main_table():
    """Their subnets sit behind an internet gateway and the NAT is on the main table.

    Ours are associated with nothing, so the main table is what they inherit - and
    failing here would block a deploy that works.
    """
    check = checker()
    check.ec2 = _ec2(
        subnets_by_az={"us-east-2a": ["subnet-a"], "us-east-2b": ["subnet-b"]},
        tables={
            "rtb-theirs": {"GatewayId": "igw-theirs", "subnets": ["subnet-a", "subnet-b"]},
            "rtb-main": {"NatGatewayId": "nat-main", "main": True},
        },
    )

    check._check_their_egress()

    assert check.results[-1].passed
    assert "nat-main" in check.results[-1].message


def test_a_named_table_that_cannot_leave_is_not_excused_by_the_main_table():
    check = checker(route_table_ids={"us-east-2a": "rtb-theirs", "us-east-2b": "rtb-theirs"})
    check.ec2 = _ec2(
        tables={
            "rtb-theirs": {"GatewayId": "igw-theirs"},
            "rtb-main": {"NatGatewayId": "nat-main", "main": True},
        }
    )

    check._check_their_egress()

    assert not check.results[-1].passed


def test_peering_is_not_egress_however_it_looks():
    """AWS routes nothing transitively over a peering connection, so the peer's NAT
    and gateway are unreachable and the packets are dropped."""
    check = checker(route_table_ids={"us-east-2a": "rtb-p", "us-east-2b": "rtb-p"})
    check.ec2 = _ec2(tables={"rtb-p": {"VpcPeeringConnectionId": "pcx-theirs"}})

    check._check_their_egress()

    assert not check.results[-1].passed
    assert "pcx-theirs" not in check.results[-1].message


def test_a_virtual_private_gateway_is_egress():
    check = checker(route_table_ids={"us-east-2a": "rtb-v", "us-east-2b": "rtb-v"})
    check.ec2 = _ec2(tables={"rtb-v": {"GatewayId": "vgw-theirs"}})

    check._check_their_egress()

    assert check.results[-1].passed
    assert "vgw-theirs" in check.results[-1].message


def test_a_range_from_another_rfc1918_block_cannot_be_associated():
    """AWS refuses a secondary CIDR outside the block the VPC numbers from."""
    check = checker(cidr="10.1.0.0/16")
    check.ec2 = _ec2(cidr_blocks=["192.168.0.0/16"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "192.168.0.0/16" in check.results[-1].message


def test_a_range_that_is_not_a_cidr_fails_the_check_instead_of_the_run():
    check = checker(cidr="not-a-cidr")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "Invalid CIDR" in check.results[-1].message


def test_a_range_too_narrow_for_the_layout_is_refused():
    check = checker(cidr="10.1.0.0/24")
    check.ec2 = _ec2(cidr_blocks=["10.0.0.0/16"])

    check._check_range_fits_their_vpc()

    assert not check.results[-1].passed
    assert "outside what is currently supported" in check.results[-1].message


def test_public_access_into_a_vpc_with_no_gateway_is_caught_before_deploying():
    check = checker(public_access=True)
    check.ec2 = _ec2(internet_gateways=[])

    check._check_igw_attached()

    assert not check.results[-1].passed
    assert "PrivateLink" in check.results[-1].details


@pytest.mark.parametrize(
    ("gateways", "expected_default"),
    [([{"InternetGatewayId": "igw-theirs"}], "Y"), ([], "n")],
)
def test_public_access_is_offered_by_what_their_vpc_can_carry(gateways, expected_default):
    wizard = object.__new__(AWSSetupWizard)
    wizard._current_step = 0
    wizard._non_interactive = False
    wizard._internet_gateway = lambda region, vpc_id: (
        gateways[0]["InternetGatewayId"] if gateways else None
    )
    asked = {}

    def prompt(message, default, **kwargs):
        asked["message"], asked["default"] = message, default
        return default

    wizard._prompt = prompt

    answer = wizard._get_public_access(vpc_id="vpc-theirs", region="us-east-2")

    assert asked["default"] == expected_default
    assert answer is bool(gateways)


def _ec2(cidr_blocks=None, tables=None, subnets_by_az=None, internet_gateways=None, subnets=None):
    """A VPC as the checks see it: subnets per zone, and the tables they use.

    tables maps an id to the route it carries plus which subnets use it, e.g.
    {"rtb-a": {"NatGatewayId": "nat-a", "subnets": ["subnet-a"]}}; "main": True marks
    the one a subnet with no association inherits.
    """
    tables: dict = tables or {}

    def as_table(table_id):
        spec = dict(tables[table_id])
        spec.pop("subnets", None)
        spec.pop("main", None)
        return {
            "RouteTableId": table_id,
            "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "State": "active", **spec}]
            if spec
            else [],
        }

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

        def describe_subnets(self, SubnetIds=None, Filters=None):
            by = {f["Name"]: f["Values"] for f in (Filters or [])}
            if SubnetIds is not None:
                wanted = SubnetIds
                return {
                    "Subnets": [
                        {"SubnetId": s, "AvailabilityZone": az, "VpcId": "vpc-theirs"}
                        for az, ids in (subnets_by_az or {}).items()
                        for s in ids
                        if s in wanted
                    ]
                }
            if "availability-zone" not in by:
                return {"Subnets": list(subnets or [])}
            az = by["availability-zone"][0]
            return {
                "Subnets": [
                    {"SubnetId": s, "AvailabilityZone": az, "VpcId": "vpc-theirs"}
                    for s in (subnets_by_az or {}).get(az, [])
                ]
            }

        def describe_route_tables(self, RouteTableIds=None, Filters=None):
            if RouteTableIds is not None:
                return {"RouteTables": [as_table(t) for t in RouteTableIds if t in tables]}
            by = {f["Name"]: f["Values"] for f in (Filters or [])}
            if "association.subnet-id" in by:
                wanted = set(by["association.subnet-id"])
                return {
                    "RouteTables": [
                        as_table(t)
                        for t, spec in tables.items()
                        if set(spec.get("subnets", [])) & wanted
                    ]
                }
            if "association.main" in by:
                return {
                    "RouteTables": [as_table(t) for t, spec in tables.items() if spec.get("main")]
                }
            return {"RouteTables": [as_table(t) for t in tables]}

        def describe_internet_gateways(self, Filters=None):
            return {"InternetGateways": internet_gateways or []}

    return Fake()


def test_a_vpc_that_permits_the_layout_passes_the_check():
    made = checker(ec2=DryRuns({}))
    made._check_permissions()

    assert made.results[-1].passed
    assert "vpc-theirs" in made.results[-1].message


def test_a_refused_subnet_fails_the_check_and_names_the_action():
    made = checker(ec2=DryRuns({"create_subnet": "UnauthorizedOperation"}))
    made._check_permissions()

    result = made.results[-1]
    assert not result.passed, "the wizard has someone to ask, so it stops"
    assert "ec2:CreateSubnet" in result.message
    assert "ec2:AssociateVpcCidrBlock" in result.details


@pytest.mark.parametrize("code", ["RequestLimitExceeded", "InvalidSubnetRange"])
def test_an_answer_that_is_not_a_refusal_does_not_fail_the_check(code):
    made = checker(ec2=DryRuns(dict.fromkeys(["create_subnet", "create_route_table"], code)))
    made._check_permissions()

    assert made.results[-1].passed


def test_the_tables_detection_would_pick_are_asked_about_too():
    """A blank map is not no tables - deploy detects them and associates them."""
    reader = _ec2(
        subnets_by_az={"us-east-2a": ["subnet-a"], "us-east-2b": ["subnet-b"]},
        tables={
            "rtb-a": {"NatGatewayId": "nat-a", "subnets": ["subnet-a"]},
            "rtb-b": {"NatGatewayId": "nat-b", "subnets": ["subnet-b"]},
            "rtb-main": {"NatGatewayId": "nat-main", "main": True},
        },
    )
    client = DryRunsReading({}, reader)

    checker(ec2=client)._check_permissions()

    asked = sorted(
        {kwargs["RouteTableId"] for op, kwargs in client.asked if op == "associate_route_table"}
    )
    assert asked == ["rtb-a", "rtb-b"], "the main table is inherited, never associated"


def test_their_route_tables_are_each_asked_about():
    client = DryRuns({})
    made = checker(ec2=client, route_table_ids={"us-east-2a": "rtb-1", "us-east-2b": "rtb-1"})
    made._check_permissions()

    asked = [
        kwargs["RouteTableId"] for name, kwargs in client.asked if name == "associate_route_table"
    ]
    assert asked == ["rtb-1"], "one probe per table, not per zone"


@pytest.mark.parametrize("public_access", [True, False], ids=["public", "private"])
def test_only_an_ingress_deploy_asks_to_make_a_route_table(public_access):
    client = DryRuns({})
    checker(ec2=client, public_access=public_access)._check_permissions()

    made_table = [name for name, _ in client.asked if name == "create_route_table"]
    assert bool(made_table) is public_access


def test_the_probe_carries_the_tags_they_asked_for():
    client = DryRuns({})
    checker(ec2=client, tags={"team": "search"})._check_permissions()

    spec = next(kwargs for name, kwargs in client.asked if name == "create_subnet")
    assert spec["TagSpecifications"][0]["Tags"] == [{"Key": "team", "Value": "search"}]


def test_a_range_that_is_not_a_cidr_is_not_reported_as_a_permission_problem():
    made = checker(cidr="not-a-cidr", ec2=DryRuns({}))
    made._check_permissions()

    assert made.results[-1].passed, "the CIDR check already failed the run by itself"
