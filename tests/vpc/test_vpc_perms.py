"""The dry run that asks their VPC whether we may build in it.

Nothing here reaches AWS: the client is a stub answering the way EC2 does.
"""

import botocore.exceptions
import pytest

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws import vpc_perms

VPC_ID = "vpc-customer"
DENIED = "UnauthorizedOperation"


def config(public_access=True, azs=("us-east-2a", "us-east-2b")):
    return AWSConfig(
        region="us-east-2",
        availability_zones=list(azs),
        vpc_cidr="10.1.0.0/20",
        existing_vpc_id=VPC_ID,
        public_access=public_access,
    )


def test_a_vpc_that_allows_the_layout_refuses_nothing(ec2):
    ec2()
    assert vpc_perms.refused(config(), VPC_ID, {}) == []


def test_a_refused_subnet_is_named_as_the_action_a_policy_grants(ec2):
    ec2(create_subnet=DENIED)
    assert vpc_perms.refused(config(), VPC_ID, {}) == ["ec2:CreateSubnet"]


def test_a_refused_security_group_is_named_too(ec2):
    ec2(create_security_group=DENIED)
    assert vpc_perms.refused(config(), VPC_ID, {}) == ["ec2:CreateSecurityGroup"]


@pytest.mark.parametrize(
    "answer",
    ["RequestLimitExceeded", "InvalidSubnetRange", botocore.exceptions.NoCredentialsError()],
    ids=["throttled", "rejected", "no-credential"],
)
def test_an_answer_that_is_not_a_refusal_does_not_stop_a_deploy(ec2, answer):
    ec2(create_subnet=answer, create_security_group=answer, create_route_table=answer)
    assert vpc_perms.refused(config(), VPC_ID, {}) == []


def test_the_probe_carries_the_tags_a_policy_may_be_conditioned_on(ec2):
    client = ec2()
    vpc_perms.refused(config(), VPC_ID, {})

    tags = client.probed("create_subnet")[0]["TagSpecifications"][0]["Tags"]
    assert {"Key": "pinecone:managed-by", "Value": "pulumi"} in tags
    assert {"Key": "kubernetes.io/role/internal-elb", "Value": "1"} in tags


def test_the_probe_asks_about_the_range_the_layout_would_cut(ec2):
    client = ec2()
    vpc_perms.refused(config(), VPC_ID, {})

    asked = client.probed("create_subnet")[0]
    assert asked["VpcId"] == VPC_ID
    assert asked["CidrBlock"] == "10.1.4.0/22"
    assert asked["AvailabilityZone"] == "us-east-2a"


@pytest.mark.parametrize("public_access", [True, False], ids=["public", "private"])
def test_only_an_ingress_deploy_asks_to_make_a_route_table(ec2, public_access):
    client = ec2()
    vpc_perms.refused(config(public_access=public_access), VPC_ID, {})
    assert bool(client.probed("create_route_table")) is public_access


def test_every_route_table_of_theirs_is_asked_about_once(ec2):
    client = ec2()
    vpc_perms.refused(
        config(azs=("us-east-2a", "us-east-2b", "us-east-2c")),
        VPC_ID,
        {"us-east-2a": "rtb-one", "us-east-2b": "rtb-one", "us-east-2c": "rtb-two"},
    )
    assert [asked["RouteTableId"] for asked in client.probed("associate_route_table")] == [
        "rtb-one",
        "rtb-two",
    ]


def test_a_route_table_we_may_not_attach_to_is_named_by_id(ec2):
    ec2(associate_route_table=DENIED)
    denied = vpc_perms.refused(config(), VPC_ID, {"us-east-2a": "rtb-theirs"})
    assert denied == ["ec2:AssociateRouteTable on rtb-theirs"]


def test_the_explanation_names_the_way_out_and_what_it_could_not_ask(ec2):
    message = vpc_perms.explain(config(), VPC_ID, ["ec2:CreateSubnet"])
    assert "ec2:CreateSubnet" in message
    assert "existing_vpc_id" in message
    assert "ec2:AssociateVpcCidrBlock" in message
    assert "pinecone:managed-by" in message


def adopting(public_access=True):
    return AWSConfig(
        region="us-east-2",
        availability_zones=["us-east-2a", "us-east-2b"],
        vpc_cidr="10.1.0.0/20",
        existing_vpc_id=VPC_ID,
        public_access=public_access,
        private_subnet_ids=["subnet-theirs-a", "subnet-theirs-b"],
    )


def test_adopting_their_subnets_asks_only_about_the_security_group(ec2):
    client = ec2()
    vpc_perms.refused(adopting(), VPC_ID, {})

    assert [name for name, _ in client.asked] == ["create_security_group"], (
        "a subnet, a route table and an association are none of them created"
    )


def test_adopting_is_not_refused_over_a_subnet_it_never_creates(ec2):
    ec2(create_subnet=DENIED, create_route_table=DENIED, associate_route_table=DENIED)
    assert vpc_perms.refused(adopting(), VPC_ID, {"us-east-2a": "rtb-theirs"}) == []


def test_adopting_still_needs_the_security_group(ec2):
    ec2(create_security_group=DENIED)
    assert vpc_perms.refused(adopting(), VPC_ID, {}) == ["ec2:CreateSecurityGroup"]


def test_the_explanation_names_the_controllers_own_role(ec2):
    message = vpc_perms.explain(adopting(), VPC_ID, ["ec2:CreateSecurityGroup"])
    assert "load balancer controller" in message
