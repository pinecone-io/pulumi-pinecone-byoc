"""The subnet layout, which every existing stack's addresses depend on."""

import ipaddress

import pytest

from pulumi_pinecone_byoc.aws.vpc import VPC

# the addresses a /16 lays out; a deployed stack has these, and an upgrade that
# computed different ones would replace its subnets
LAYOUT_16 = {
    True: ["10.0.0.0/20", "10.0.16.0/20", "10.0.32.0/20"],
    False: ["10.0.64.0/18", "10.0.128.0/18", "10.0.192.0/18"],
}


@pytest.mark.parametrize("is_public", [True, False], ids=["public", "private"])
def test_a_16_lays_out_where_deployed_stacks_already_have_their_subnets(is_public):
    assert [str(VPC._calculate_cidr("10.0.0.0/16", i, is_public)) for i in range(3)] == LAYOUT_16[
        is_public
    ]


def test_a_20_scales_the_same_layout_down():
    assert [str(VPC._calculate_cidr("192.168.16.0/20", i, True)) for i in range(3)] == [
        "192.168.16.0/26",
        "192.168.17.0/26",
        "192.168.18.0/26",
    ]
    assert [str(VPC._calculate_cidr("192.168.16.0/20", i, False)) for i in range(3)] == [
        "192.168.20.0/22",
        "192.168.24.0/22",
        "192.168.28.0/22",
    ]


@pytest.mark.parametrize("vpc_cidr", ["10.0.0.0/16", "10.1.0.0/18", "192.168.16.0/20"])
def test_subnets_stay_inside_the_vpc_and_never_overlap(vpc_cidr):
    vpc = ipaddress.IPv4Network(vpc_cidr)
    subnets = [
        VPC._calculate_cidr(vpc_cidr, i, public) for public in (True, False) for i in range(3)
    ]
    for subnet in subnets:
        assert subnet.subnet_of(vpc), f"{subnet} escapes {vpc_cidr}"
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
        VPC._validate_cidr(cidr)


@pytest.mark.parametrize("vpc_cidr", ["192.168.16.0/20", "10.1.0.0/18"])
def test_a_public_subnet_stays_within_what_aws_will_load_balance_in(vpc_cidr):
    """AWS requires a /27 or wider with eight free addresses, or an ALB 5xxes as it scales."""
    for index in range(3):
        public = VPC._calculate_cidr(vpc_cidr, index, True)
        assert public.prefixlen <= 27, f"{public} is narrower than AWS allows for an ALB"
        assert public.num_addresses - 5 >= 8 + 1, "eight free for the ALB, one for the NAT"
