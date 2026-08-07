"""A carved network gets public access the same way a module-built one does.

The subnets are the module's either way; in a customer VPC the internet gateway is
theirs, so the only thing that can differ is whether the deploy asked for ingress
at all. That answer travels from the generated program's `public-access-enabled`
config to the VPC component, and a flag dropped in between is invisible until an
hour of deploy ends with no load balancer.

Whether the carve path then reaches for a public subnet needs a live provider - it
looks up the customer's internet gateway - so that step belongs to the e2e tier.
"""

import ipaddress

import pytest

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws.cluster import PineconeAWSCluster, PineconeAWSClusterArgs
from pulumi_pinecone_byoc.aws.vpc import VPC


def build_config(**kwargs):
    args = PineconeAWSClusterArgs(pinecone_api_key="unused", pinecone_version="unused", **kwargs)
    return PineconeAWSCluster._build_config(object.__new__(PineconeAWSCluster), args)


@pytest.mark.parametrize("asked_for_ingress", [True, False], ids=["public", "private"])
def test_the_deploys_answer_reaches_the_vpc_config(asked_for_ingress):
    config = build_config(public_access_enabled=asked_for_ingress)
    assert config.public_access is asked_for_ingress


def test_carved_public_and_private_subnets_do_not_overlap():
    config = AWSConfig(
        region="us-east-2",
        availability_zones=["us-east-2a", "us-east-2b"],
        vpc_cidr="10.1.0.0/16",
    )
    vpc = object.__new__(VPC)
    vpc.config = config

    ranges = [
        ipaddress.IPv4Network(vpc._calculate_cidr(i, is_public=is_public))
        for i in range(len(config.availability_zones))
        for is_public in (True, False)
    ]
    carve = ipaddress.IPv4Network(config.vpc_cidr)
    for net in ranges:
        assert net.subnet_of(carve), f"{net} must come out of the carve range {carve}"
    for i, net in enumerate(ranges):
        for other in ranges[i + 1 :]:
            assert not net.overlaps(other), f"{net} overlaps {other}"
