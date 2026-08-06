"""Which network the module builds is decided by what the stack config supplies."""

import pytest

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws.vpc import VPC


def config(**kwargs):
    return AWSConfig(region="us-east-2", availability_zones=["us-east-2a"], **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "_create"),
        ({"existing_vpc_id": "vpc-1"}, "_adopt_and_create_subnets"),
        ({"existing_vpc_id": "vpc-1", "private_subnet_ids": ["subnet-1"]}, "_adopt"),
        ({"existing_vpc_id": "vpc-1", "public_subnet_ids": ["subnet-2"]}, "_adopt"),
    ],
    ids=["vanilla", "carve", "adopt-private", "adopt-public"],
)
def test_the_mode_follows_the_inputs(monkeypatch, kwargs, expected):
    called = []
    for name in ("_create", "_adopt_and_create_subnets", "_adopt"):
        monkeypatch.setattr(VPC, name, lambda self, *a, n=name, **k: called.append(n))
    vpc = object.__new__(VPC)
    VPC.__init__(vpc, "pc-vpc", config(**kwargs))
    assert called == [expected]
