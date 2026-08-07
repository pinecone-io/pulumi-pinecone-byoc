"""Build the carve network in-process, against pulumi's mock engine.

The carve path talks to the provider - it looks up the customer's VPC and their
internet gateway - so nothing else in the unit suite constructs it, and a call
that does not exist surfaces only in an e2e run. These mocks answer the invokes,
so the component is built here for the cost of a unit test.
"""

import pulumi
import pytest

from config.aws import AWSConfig

VPC_ID = "vpc-customer"
IGW_ID = "igw-customer"
GET_VPC = "aws:ec2/getVpc:getVpc"
GET_IGW = "aws:ec2/getInternetGateway:getInternetGateway"
NAT = "aws:ec2/natGateway:NatGateway"
EIP = "aws:ec2/eip:Eip"
RTA = "aws:ec2/routeTableAssociation:RouteTableAssociation"


class Engine(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources = []
        self.invokes = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append((args.typ, args.name, args.inputs))
        return f"{args.name}-id", args.inputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        self.invokes.append(args.token)
        if args.token == GET_VPC:
            return {"id": VPC_ID, "cidrBlockAssociations": [{"cidrBlock": "10.0.0.0/16"}]}
        if args.token == GET_IGW:
            return {"id": IGW_ID, "internetGatewayId": IGW_ID}
        return {}


@pytest.fixture
def engine():
    mocks = Engine()
    pulumi.runtime.set_mocks(mocks, preview=False)
    return mocks


def carve(public_access, azs=("us-east-2a", "us-east-2b"), route_tables=None):
    from pulumi_pinecone_byoc.aws.vpc import VPC

    return VPC(
        "pc-vpc",
        AWSConfig(
            region="us-east-2",
            availability_zones=list(azs),
            vpc_cidr="10.1.0.0/16",
            existing_vpc_id=VPC_ID,
            public_access=public_access,
            existing_route_table_ids=route_tables,
        ),
    )


def build(**kwargs):
    @pulumi.runtime.test
    def run():
        carve(**kwargs)

    run()


def created(engine, kind):
    return [r for r in engine.resources if r[0] == kind]


@pulumi.runtime.test
def test_ingress_gets_a_public_subnet_per_az_routed_at_their_gateway(engine):
    vpc = carve(public_access=True)

    assert len(vpc.public_subnets) == 2, "an ALB needs a subnet in every AZ it serves"
    assert len(vpc.private_subnets) == 2
    assert GET_IGW in engine.invokes, "the customer's gateway is what makes the subnet public"

    def routes_at_their_gateway(args):
        destination, gateway = args
        assert destination == "0.0.0.0/0"
        assert gateway == IGW_ID, "the route must point at the customer's own gateway"

    return pulumi.Output.all(
        vpc.public_route.destination_cidr_block, vpc.public_route.gateway_id
    ).apply(routes_at_their_gateway)


def test_no_ingress_asks_for_no_gateway_and_carves_nothing_public(engine):
    vpc = carve(public_access=False)

    assert vpc.public_subnets == []
    assert len(vpc.private_subnets) == 2
    assert GET_IGW not in engine.invokes, "a private carve must not require a gateway"
    assert not hasattr(vpc, "public_route"), "no gateway route without a public subnet to use it"


def test_a_carve_builds_no_egress_of_its_own(engine):
    """Absence is only assertable once every registration has landed, and they land
    asynchronously - a snapshot taken inside an apply misses whatever registers after
    it. Building under the test decorator and asserting on its return drains them all.
    """
    build(public_access=False)

    assert created(engine, NAT) == [], "egress in an adopted VPC is the customer's to route"
    assert created(engine, EIP) == []


@pulumi.runtime.test
def test_private_subnets_are_associated_with_the_route_table_named_for_their_az(engine):
    tables = {"us-east-2a": "rtb-theirs-a", "us-east-2b": "rtb-theirs-b"}

    vpc = carve(public_access=False, route_tables=tables)

    assert len(vpc.private_route_table_associations) == 2

    def routed_through_their_tables(ids):
        assert list(ids) == ["rtb-theirs-a", "rtb-theirs-b"], (
            "a subnet reaches the internet only through the table given for its own AZ"
        )

    return pulumi.Output.all(
        *[a.route_table_id for a in vpc.private_route_table_associations]
    ).apply(routed_through_their_tables)


def test_without_route_tables_the_private_subnets_inherit_the_vpc_main_table(engine):
    vpc = carve(public_access=False)

    assert vpc.private_route_table_associations == [], (
        "associating nothing is what leaves the subnets on the main route table"
    )


def test_a_route_table_missing_for_one_az_is_refused_by_name(engine):
    with pytest.raises(ValueError, match="us-east-2b"):
        carve(public_access=False, route_tables={"us-east-2a": "rtb-theirs-a"})


def test_a_vpc_without_a_gateway_is_refused_with_the_alternative_named(engine, monkeypatch):
    import pulumi_aws as aws

    def no_gateway(*args, **kwargs):
        raise Exception("no matching EC2 Internet Gateway found")

    monkeypatch.setattr(aws.ec2, "get_internet_gateway", no_gateway)

    with pytest.raises(ValueError, match="PrivateLink"):
        carve(public_access=True)


def test_a_lookup_that_failed_for_another_reason_is_not_reported_as_a_missing_gateway(
    engine, monkeypatch
):
    import pulumi_aws as aws

    def throttled(*args, **kwargs):
        raise Exception("RequestLimitExceeded: Request limit exceeded")

    monkeypatch.setattr(aws.ec2, "get_internet_gateway", throttled)

    with pytest.raises(Exception, match="RequestLimitExceeded"):
        carve(public_access=True)
