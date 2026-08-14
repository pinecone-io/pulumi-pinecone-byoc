"""The VPC, everything about it that can be decided without deploying one.

The layout is arithmetic. The rest is the component and the cluster built against
pulumi's mock engine, which answers the provider lookups the existing-VPC path
makes - so a call that does not exist fails here rather than only in a cloud run.
Anything that needs a real `pulumi up` is in test_vpc_live.py.
"""

import ipaddress

import pulumi
import pytest
from wizard import AWSSetupWizard

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws import vpc_subnet
from pulumi_pinecone_byoc.aws.cluster import PineconeAWSCluster, PineconeAWSClusterArgs

VPC_ID = "vpc-customer"
IGW_ID = "igw-customer"
GET_VPC = "aws:ec2/getVpc:getVpc"
GET_IGW = "aws:ec2/getInternetGateway:getInternetGateway"
GET_ROUTE_TABLES = "aws:ec2/getRouteTables:getRouteTables"
GET_ROUTE_TABLE = "aws:ec2/getRouteTable:getRouteTable"
GET_SUBNETS = "aws:ec2/getSubnets:getSubnets"
VPC = "aws:ec2/vpc:Vpc"
SUBNET = "aws:ec2/subnet:Subnet"
NAT = "aws:ec2/natGateway:NatGateway"
EIP = "aws:ec2/eip:Eip"
RTA = "aws:ec2/routeTableAssociation:RouteTableAssociation"


def their_subnet(az):
    return f"subnet-theirs-{az}"


def their_table(table_id, az, egress, main=False):
    return {
        "routeTableId": table_id,
        "associations": [
            {"subnetId": None if main else their_subnet(az), "main": main},
        ],
        "routes": [{"cidrBlock": "0.0.0.0/0", **egress}],
    }


class Engine(pulumi.runtime.Mocks):
    def __init__(self, tables=None):
        self.resources = []
        self.invokes = []
        # by default they run a NAT per AZ on a private table of their own
        self.tables = (
            tables
            if tables is not None
            else {
                "rtb-theirs-a": their_table(
                    "rtb-theirs-a", "us-east-2a", {"natGatewayId": "nat-a"}
                ),
                "rtb-theirs-b": their_table(
                    "rtb-theirs-b", "us-east-2b", {"natGatewayId": "nat-b"}
                ),
            }
        )

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append((args.typ, args.name, args.inputs))
        return f"{args.name}-id", args.inputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        self.invokes.append(args.token)
        if args.token == GET_VPC:
            return {
                "id": VPC_ID,
                "cidrBlock": "10.0.0.0/16",
                "cidrBlockAssociations": [{"cidrBlock": "10.0.0.0/16"}],
            }
        if args.token == GET_IGW:
            return {"id": IGW_ID, "internetGatewayId": IGW_ID}
        if args.token == GET_ROUTE_TABLES:
            return {"ids": list(self.tables)}
        if args.token == GET_ROUTE_TABLE:
            return self.tables[args.args["routeTableId"]]
        if args.token == GET_SUBNETS:
            az = next(
                f["values"][0] for f in args.args["filters"] if f["name"] == "availability-zone"
            )
            return {"ids": [their_subnet(az)]}
        return {}


@pytest.fixture(autouse=True)
def permitted(ec2):
    """Their VPC allows everything, unless a test says otherwise."""
    return ec2()


@pytest.fixture
def engine():
    mocks = Engine()
    pulumi.runtime.set_mocks(mocks, preview=False)
    return mocks


def engine_with(tables):
    mocks = Engine(tables=tables)
    pulumi.runtime.set_mocks(mocks, preview=False)
    return mocks


def in_existing_vpc(public_access, azs=("us-east-2a", "us-east-2b"), route_tables=None):
    from pulumi_pinecone_byoc.aws.vpc import VPC

    return VPC(
        "pc-vpc",
        AWSConfig(
            region="us-east-2",
            availability_zones=list(azs),
            vpc_cidr="10.1.0.0/20",
            existing_vpc_id=VPC_ID,
            public_access=public_access,
            existing_route_table_ids=route_tables,
        ),
    )


def build(**kwargs):
    @pulumi.runtime.test
    def run():
        in_existing_vpc(**kwargs)

    run()


def created(engine, kind):
    return [r for r in engine.resources if r[0] == kind]


@pulumi.runtime.test
def test_ingress_gets_a_public_subnet_per_az_routed_at_their_gateway(engine):
    vpc = in_existing_vpc(public_access=True)

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


def test_no_ingress_asks_for_no_gateway_and_creates_nothing_public(engine):
    vpc = in_existing_vpc(public_access=False)

    assert vpc.public_subnets == []
    assert len(vpc.private_subnets) == 2
    assert GET_IGW not in engine.invokes, "a private deploy must not require a gateway"
    assert not hasattr(vpc, "public_route"), "no gateway route without a public subnet to use it"


def test_the_module_builds_no_egress_of_its_own(engine):
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

    vpc = in_existing_vpc(public_access=False, route_tables=tables)

    assert len(vpc.private_route_table_associations) == 2

    def routed_through_their_tables(ids):
        assert list(ids) == ["rtb-theirs-a", "rtb-theirs-b"], (
            "a subnet reaches the internet only through the table given for its own AZ"
        )

    return pulumi.Output.all(
        *[a.route_table_id for a in vpc.private_route_table_associations]
    ).apply(routed_through_their_tables)


@pulumi.runtime.test
def test_the_table_their_own_subnets_egress_through_is_detected(engine):
    vpc = in_existing_vpc(public_access=False)

    assert len(vpc.private_route_table_associations) == 2, (
        "given no route tables, the ones their subnets in each AZ already use are found"
    )

    def routed_through_their_tables(ids):
        assert list(ids) == ["rtb-theirs-a", "rtb-theirs-b"]

    return pulumi.Output.all(
        *[a.route_table_id for a in vpc.private_route_table_associations]
    ).apply(routed_through_their_tables)


def test_a_vpc_with_no_egress_of_their_own_leaves_the_subnets_on_the_main_table():
    engine_with({})

    vpc = in_existing_vpc(public_access=False)

    assert vpc.private_route_table_associations == [], (
        "associating nothing is what leaves the subnets on the main route table"
    )


def test_egress_on_the_main_table_is_inherited_rather_than_associated():
    engine_with(
        {
            "rtb-main": {
                "routeTableId": "rtb-main",
                "associations": [{"subnetId": their_subnet("us-east-2a"), "main": True}],
                "routes": [{"cidrBlock": "0.0.0.0/0", "natGatewayId": "nat-a"}],
            }
        }
    )

    vpc = in_existing_vpc(public_access=False, azs=("us-east-2a",))

    assert vpc.private_route_table_associations == [], (
        "a subnet with no association already uses the main table"
    )


def test_a_public_table_of_theirs_is_not_mistaken_for_egress():
    engine_with(
        {"rtb-public": their_table("rtb-public", "us-east-2a", {"gatewayId": "igw-theirs"})}
    )

    vpc = in_existing_vpc(public_access=False, azs=("us-east-2a",))

    assert vpc.private_route_table_associations == [], (
        "an internet gateway is how their public subnets get out, not their private ones"
    )


def test_two_egress_tables_in_one_az_asks_to_be_told_which():
    engine_with(
        {
            "rtb-nat": their_table("rtb-nat", "us-east-2a", {"natGatewayId": "nat-a"}),
            "rtb-tgw": their_table("rtb-tgw", "us-east-2a", {"transitGatewayId": "tgw-a"}),
        }
    )

    with pytest.raises(ValueError, match="us-east-2a"):
        in_existing_vpc(public_access=False, azs=("us-east-2a",))


def test_a_route_table_missing_for_one_az_is_refused_by_name(engine):
    with pytest.raises(ValueError, match="us-east-2b"):
        in_existing_vpc(public_access=False, route_tables={"us-east-2a": "rtb-theirs-a"})


def test_a_vpc_without_a_gateway_is_refused_with_the_alternative_named(engine, monkeypatch):
    import pulumi_aws as aws

    def no_gateway(*args, **kwargs):
        raise Exception("no matching EC2 Internet Gateway found")

    monkeypatch.setattr(aws.ec2, "get_internet_gateway", no_gateway)

    with pytest.raises(ValueError, match="PrivateLink"):
        in_existing_vpc(public_access=True)


def test_a_lookup_that_failed_for_another_reason_is_not_reported_as_a_missing_gateway(
    engine, monkeypatch
):
    import pulumi_aws as aws

    def throttled(*args, **kwargs):
        raise Exception("RequestLimitExceeded: Request limit exceeded")

    monkeypatch.setattr(aws.ec2, "get_internet_gateway", throttled)

    with pytest.raises(Exception, match="RequestLimitExceeded"):
        in_existing_vpc(public_access=True)


def test_a_vpc_that_refuses_our_subnets_warns_and_deploys_anyway(engine, ec2, monkeypatch):
    ec2(create_subnet="UnauthorizedOperation")
    warnings = []
    monkeypatch.setattr(pulumi.log, "warn", lambda message, *_, **__: warnings.append(message))

    vpc = in_existing_vpc(public_access=False)

    assert len(vpc.private_subnets) == 2
    assert "ec2:CreateSubnet" in warnings[0]


def cluster(**kwargs):
    return PineconeAWSCluster(
        name="pc",
        args=PineconeAWSClusterArgs(
            pinecone_api_key="unused",
            pinecone_version="unused",
            region="us-east-2",
            availability_zones=["us-east-2a", "us-east-2b"],
            **kwargs,
        ),
    )


def types_of(engine):
    return [resource[0] for resource in engine.resources]


def test_a_network_only_cluster_builds_the_vpc(engine):
    @pulumi.runtime.test
    def run():
        cluster(network_only=True)

    run()

    assert types_of(engine).count(VPC) == 1
    assert types_of(engine).count(SUBNET) == 4, "a public and a private subnet in each of two AZs"


def test_a_network_only_cluster_registers_no_environment(engine):
    @pulumi.runtime.test
    def run():
        cluster(network_only=True)

    run()

    names = [resource[1] for resource in engine.resources]
    assert "pc-environment" not in names, (
        "the network needs no control-plane identity, and registering one would "
        "make this deploy need a Pinecone API key"
    )


def test_a_network_only_cluster_exposes_the_subnets_a_later_deploy_needs(engine):
    @pulumi.runtime.test
    def run():
        built = cluster(network_only=True)
        assert built.vpc_id is not None
        assert len(built.private_subnet_ids) == 2
        assert len(built.public_subnet_ids) == 2

    run()


def test_network_only_is_off_by_default():
    args = PineconeAWSClusterArgs(pinecone_api_key="unused", pinecone_version="unused")
    assert args.network_only is False, "a normal deploy must still build the whole cluster"


def build_config(**kwargs):
    args = PineconeAWSClusterArgs(pinecone_api_key="unused", pinecone_version="unused", **kwargs)
    return PineconeAWSCluster._build_config(object.__new__(PineconeAWSCluster), args)


@pytest.mark.parametrize("asked_for_ingress", [True, False], ids=["public", "private"])
def test_the_deploys_answer_reaches_the_vpc_config(asked_for_ingress):
    config = build_config(public_access_enabled=asked_for_ingress)
    assert config.public_access is asked_for_ingress


# the addresses a /16 lays out; a deployed stack has these, and an upgrade that
# computed different ones would replace its subnets
LAYOUT_16 = {
    True: ["10.0.0.0/20", "10.0.16.0/20", "10.0.32.0/20"],
    False: ["10.0.64.0/18", "10.0.128.0/18", "10.0.192.0/18"],
}


@pytest.mark.parametrize("is_public", [True, False], ids=["public", "private"])
def test_a_16_lays_out_where_deployed_stacks_already_have_their_subnets(is_public):
    assert [str(vpc_subnet.cidr("10.0.0.0/16", i, is_public)) for i in range(3)] == LAYOUT_16[
        is_public
    ]


def test_a_20_scales_the_same_layout_down():
    assert [str(vpc_subnet.cidr("192.168.16.0/20", i, True)) for i in range(3)] == [
        "192.168.16.0/26",
        "192.168.17.0/26",
        "192.168.18.0/26",
    ]
    assert [str(vpc_subnet.cidr("192.168.16.0/20", i, False)) for i in range(3)] == [
        "192.168.20.0/22",
        "192.168.24.0/22",
        "192.168.28.0/22",
    ]


@pytest.mark.parametrize("vpc_cidr", ["10.0.0.0/16", "10.1.0.0/18", "192.168.16.0/20"])
def test_subnets_stay_inside_the_vpc_and_never_overlap(vpc_cidr):
    vpc = ipaddress.IPv4Network(vpc_cidr)
    subnets = [vpc_subnet.cidr(vpc_cidr, i, public) for public in (True, False) for i in range(3)]
    for net in subnets:
        assert net.subnet_of(vpc), f"{net} escapes {vpc_cidr}"
    for i, a in enumerate(subnets):
        for b in subnets[i + 1 :]:
            assert not a.overlaps(b), f"{a} overlaps {b}"


@pytest.mark.parametrize(
    ("cidr", "reason"),
    [
        ("192.168.16.0/21", "between a /16 and a /20"),
        ("10.0.0.0/8", "between a /16 and a /20"),
        ("11.0.0.0/16", "RFC 1918"),
    ],
)
def test_ranges_we_will_not_lay_out(cidr, reason):
    with pytest.raises(ValueError, match=reason):
        vpc_subnet.validate_vpc_cidr(cidr)


@pytest.mark.parametrize("vpc_cidr", ["192.168.16.0/20", "10.1.0.0/18"])
def test_a_public_subnet_stays_within_what_aws_will_load_balance_in(vpc_cidr):
    """AWS requires a /27 or wider with eight free addresses, or an ALB 5xxes as it scales."""
    for index in range(3):
        public = vpc_subnet.cidr(vpc_cidr, index, True)
        assert public.prefixlen <= 27, f"{public} is narrower than AWS allows for an ALB"
        assert public.num_addresses - 5 >= 8 + 1, "eight free for the ALB, one for the NAT"


def test_the_default_the_wizard_offers_is_a_range_the_vpc_will_lay_out():
    vpc_subnet.validate_vpc_cidr(AWSSetupWizard.DEFAULT_CIDR)
    for index in range(3):
        for is_public in (True, False):
            net = vpc_subnet.cidr(AWSSetupWizard.DEFAULT_CIDR, index, is_public)
            assert net.subnet_of(ipaddress.IPv4Network(AWSSetupWizard.DEFAULT_CIDR))
