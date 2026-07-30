"""Which URNs a network-only apply targets. Pure selection, no infrastructure."""

from e2e.plan import network_targets

STACK = "urn:pulumi:dev::pinecone-byoc"
CLUSTER = "pinecone:byoc:PineconeAWSCluster"
VPC = f"{CLUSTER}$pinecone:byoc:VPC"


def urn(chain, name):
    return f"{STACK}::{chain}::{name}"


TARGETED = [
    urn(CLUSTER, "pinecone-aws-cluster"),
    urn(VPC, "pc-vpc"),
    urn(f"{VPC}$aws:ec2/vpc:Vpc", "pc-vpc"),
    urn(f"{VPC}$aws:ec2/subnet:Subnet", "pc-vpc-private-us-east-2a"),
    urn(f"{VPC}$aws:ec2/vpcIpv4CidrBlockAssociation:VpcIpv4CidrBlockAssociation", "pc-vpc-cidr"),
    urn(f"{VPC}$aws:ec2/natGateway:NatGateway", "pc-vpc-nat-us-east-2a"),
    urn(f"{VPC}$aws:ec2/vpcEndpoint:VpcEndpoint", "pc-vpc-s3-endpoint"),
]

# providers and the root stack are targeted implicitly by the engine
SKIPPED = [
    urn("pulumi:pulumi:Stack", "pinecone-byoc-dev"),
    urn("pulumi:providers:aws", "default_6_83_0"),
    urn("pulumi:providers:kubernetes", "default"),
    urn(f"{CLUSTER}$pinecone:byoc:RDS", "pc-rds"),
    urn(f"{CLUSTER}$pinecone:byoc:RDS$aws:rds/cluster:Cluster", "pc-control-db"),
    urn(f"{CLUSTER}$pinecone:byoc:EKS$aws:eks/cluster:Cluster", "pc-eks"),
    urn(f"{CLUSTER}$aws:ec2/securityGroup:SecurityGroup", "pc-node-sg"),
    urn(f"{CLUSTER}$pinecone:byoc:DatadogApiKey", "pc-datadog-key"),
    "not-a-urn",
]


def plan(urns):
    return {"steps": [{"op": "create", "urn": u} for u in urns]}


def test_the_vpc_subtree_and_its_ancestors_are_targeted():
    assert network_targets(plan(TARGETED)) == TARGETED


def test_everything_else_is_skipped():
    assert network_targets(plan(SKIPPED)) == []


def test_a_mixed_plan_keeps_only_the_network():
    assert network_targets(plan(SKIPPED + TARGETED)) == TARGETED


def test_our_dynamic_resources_are_not_mistaken_for_ancestor_components():
    datadog = urn(f"{CLUSTER}$pinecone:byoc:DatadogApiKey", "pc-datadog-key")
    assert network_targets(plan([urn(VPC, "pc-vpc"), datadog])) == [urn(VPC, "pc-vpc")]


def test_a_plan_without_steps_targets_nothing():
    assert network_targets({}) == []
