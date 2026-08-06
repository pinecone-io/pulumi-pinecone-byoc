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


def carve(public_access, azs=("us-east-2a", "us-east-2b")):
    from pulumi_pinecone_byoc.aws.vpc import VPC

    return VPC(
        "pc-vpc",
        AWSConfig(
            region="us-east-2",
            availability_zones=list(azs),
            vpc_cidr="10.1.0.0/16",
            existing_vpc_id=VPC_ID,
            public_access=public_access,
        ),
    )


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
    assert created(engine, "aws:ec2/route:Route") == []


def test_a_vpc_without_a_gateway_is_refused_with_the_alternative_named(engine, monkeypatch):
    import pulumi_aws as aws

    def no_gateway(*args, **kwargs):
        raise Exception("no matching EC2 Internet Gateway found")

    monkeypatch.setattr(aws.ec2, "get_internet_gateway", no_gateway)

    with pytest.raises(ValueError, match="PrivateLink"):
        carve(public_access=True)
