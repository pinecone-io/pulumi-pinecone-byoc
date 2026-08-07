"""The egress preflight, which is the only thing standing between a carve deploy
and a cluster whose nodes cannot reach a registry.

Carved private subnets get no NAT of our own - the module builds none in an adopted
VPC - so egress is whatever the customer's routing already does. That makes this
check, not the VPC component, the place a missing default route has to surface.
"""

import boto3
import pytest
from wizard import AWSPreflightChecker

VPC_ID = "vpc-customer"
MAIN_TABLE_FILTERS = [
    {"Name": "vpc-id", "Values": [VPC_ID]},
    {"Name": "association.main", "Values": ["true"]},
]


class FakeEC2:
    def __init__(self, tables=None, error=None):
        self.tables = tables if tables is not None else []
        self.error = error
        self.filters = None

    def describe_route_tables(self, Filters=None):  # noqa: N803 - boto3's own casing
        self.filters = Filters
        if self.error:
            raise self.error
        return {"RouteTables": self.tables}


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.setattr(boto3, "client", lambda *a, **k: None)

    def build(ec2):
        c = AWSPreflightChecker("us-east-2", ["us-east-2a"], "10.1.0.0/16", vpc_id=VPC_ID)
        c.ec2 = ec2
        return c

    return build


def egress(checker, tables=None, error=None):
    c = checker(FakeEC2(tables, error))
    c._check_carve_egress()
    return c.results[-1], c.ec2


def table(*routes):
    return [{"Routes": list(routes)}]


def route(target_key="NatGatewayId", target="nat-1", cidr="0.0.0.0/0", state="active"):
    return {"DestinationCidrBlock": cidr, "State": state, target_key: target}


def test_wizard_preflight_fails_on_no_egress(checker):
    result, _ = egress(checker, table(route(cidr="10.0.0.0/16")))

    assert not result.passed
    assert "no default (0.0.0.0/0) route" in result.message
    assert "NAT" in result.details and "Transit Gateway" in result.details


def test_wizard_preflight_fails_when_the_default_route_is_blackholed(checker):
    result, _ = egress(checker, table(route(state="blackhole")))

    assert not result.passed, "a route to a deleted NAT carries no traffic"


def test_wizard_preflight_fails_when_the_vpc_has_no_main_route_table(checker):
    result, _ = egress(checker, [])

    assert not result.passed
    assert VPC_ID in result.message


def test_wizard_preflight_passes_and_names_what_it_egresses_through(checker):
    result, _ = egress(checker, table(route()))

    assert result.passed
    assert "nat-1" in result.message, "the operator should see which target was accepted"


def test_wizard_preflight_accepts_a_transit_gateway_as_egress(checker):
    result, _ = egress(checker, table(route("TransitGatewayId", "tgw-1")))

    assert result.passed, "a carve VPC need not egress through a NAT"
    assert "tgw-1" in result.message


def test_wizard_preflight_asks_only_about_the_main_table_of_this_vpc(checker):
    _, ec2 = egress(checker, table(route()))

    assert ec2.filters == MAIN_TABLE_FILTERS


def test_wizard_preflight_fails_closed_when_the_lookup_errors(checker):
    result, _ = egress(checker, error=RuntimeError("RequestLimitExceeded"))

    assert not result.passed, "an unanswered egress question must not read as egress"
    assert "RequestLimitExceeded" in result.details
