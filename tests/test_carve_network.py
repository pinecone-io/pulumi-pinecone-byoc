"""Build the carve network in-process, against pulumi's mock engine.

The carve path talks to the provider - it looks up the customer's VPC - so nothing
else in the unit suite constructs it, and a call that does not exist surfaces only
in an e2e run. These mocks answer the invokes, so the component is built here for
the cost of a unit test.
"""

import pulumi
import pytest

from config.aws import AWSConfig

VPC_ID = "vpc-customer"
GET_VPC = "aws:ec2/getVpc:getVpc"
NAT = "aws:ec2/natGateway:NatGateway"
EIP = "aws:ec2/eip:Eip"


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
        return {}


@pytest.fixture
def engine():
    mocks = Engine()
    pulumi.runtime.set_mocks(mocks, preview=False)
    return mocks


def carve(azs=("us-east-2a", "us-east-2b"), route_tables=None):
    from pulumi_pinecone_byoc.aws.vpc import VPC

    return VPC(
        "pc-vpc",
        AWSConfig(
            region="us-east-2",
            availability_zones=list(azs),
            vpc_cidr="10.1.0.0/16",
            existing_vpc_id=VPC_ID,
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


def test_a_carve_builds_no_egress_of_its_own(engine):
    """Absence is only assertable once every registration has landed, and they land
    asynchronously - a snapshot taken inside an apply misses whatever registers after
    it. Building under the test decorator and asserting on its return drains them all.
    """
    build()

    assert created(engine, NAT) == [], "egress in an adopted VPC is the customer's to route"
    assert created(engine, EIP) == []


@pulumi.runtime.test
def test_private_subnets_are_associated_with_the_route_table_named_for_their_az(engine):
    tables = {"us-east-2a": "rtb-theirs-a", "us-east-2b": "rtb-theirs-b"}

    vpc = carve(route_tables=tables)

    assert len(vpc.private_route_table_associations) == 2

    def routed_through_their_tables(ids):
        assert list(ids) == ["rtb-theirs-a", "rtb-theirs-b"], (
            "a subnet reaches the internet only through the table given for its own AZ"
        )

    return pulumi.Output.all(
        *[a.route_table_id for a in vpc.private_route_table_associations]
    ).apply(routed_through_their_tables)


def test_without_route_tables_the_private_subnets_inherit_the_vpc_main_table(engine):
    vpc = carve()

    assert vpc.private_route_table_associations == [], (
        "associating nothing is what leaves the subnets on the main route table"
    )


def test_a_route_table_missing_for_one_az_is_refused_by_name(engine):
    with pytest.raises(ValueError, match="us-east-2b"):
        carve(route_tables={"us-east-2a": "rtb-theirs-a"})
