import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig

MIN_VPC_PREFIX = 16
MAX_VPC_PREFIX = 20

# AWS refuses to load balance in anything narrower than a /27, and wants eight
# addresses free to scale into
PUBLIC_PREFIX_ON_A_SLICE = 26

# the VPC is cut into sixteen slots; a public subnet takes one, a private subnet four
SLOT_BITS = 4
PRIVATE_SLOTS = 4
PRIVATE_FIRST_SLOT = 4

RFC1918_RANGES = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
]

MANAGED_BY = ("pinecone:managed-by", "pulumi")


def validate_vpc_cidr(cidr: str) -> None:
    try:
        network = ipaddress.IPv4Network(cidr)
    except (ValueError, ipaddress.AddressValueError) as e:
        raise ValueError(f"Invalid VPC CIDR '{cidr}': {e}") from e

    if not MIN_VPC_PREFIX <= network.prefixlen <= MAX_VPC_PREFIX:
        raise ValueError(
            f"VPC CIDR must be between a /{MIN_VPC_PREFIX} and a /{MAX_VPC_PREFIX} "
            f"(got /{network.prefixlen}). The subnet layout needs sixteen slots, and a "
            f"/{MAX_VPC_PREFIX} is the smallest range that still leaves a workable subnet "
            "per availability zone."
        )

    if not any(network.subnet_of(rfc1918) for rfc1918 in RFC1918_RANGES):
        raise ValueError(
            f"VPC CIDR '{cidr}' is not in an RFC 1918 private range. "
            "Use a block inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16. "
            "See https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html"
        )


def cidr(vpc_cidr: str, index: int, is_public: bool) -> ipaddress.IPv4Network:
    network = ipaddress.IPv4Network(vpc_cidr)
    slots = list(network.subnets(prefixlen_diff=SLOT_BITS))

    if is_public:
        slot = slots[index]
        prefix = (
            network.prefixlen + SLOT_BITS
            if network.prefixlen == MIN_VPC_PREFIX
            else max(PUBLIC_PREFIX_ON_A_SLICE, slot.prefixlen)
        )
    else:
        slot = slots[PRIVATE_FIRST_SLOT + index * PRIVATE_SLOTS]
        prefix = network.prefixlen + PRIVATE_SLOTS // 2

    return ipaddress.IPv4Network((slot.network_address, prefix))


def validate_range_is_free(config: AWSConfig, vpc_id: str) -> None:
    kinds = (True, False) if config.public_access else (False,)
    ours = [
        (cidr(config.vpc_cidr, index, is_public), az, "public" if is_public else "private")
        for index, az in enumerate(config.availability_zones)
        for is_public in kinds
    ]

    key, value = MANAGED_BY
    taken = []
    for subnet_id in aws.ec2.get_subnets(filters=[{"name": "vpc-id", "values": [vpc_id]}]).ids:
        theirs = aws.ec2.get_subnet(id=subnet_id)
        if theirs.tags.get(key) == value:
            continue
        occupied = ipaddress.IPv4Network(theirs.cidr_block)
        taken += [
            f"{net} ({kind} {az}) overlaps {occupied} ({subnet_id})"
            for net, az, kind in ours
            if net.overlaps(occupied)
        ]

    if taken:
        raise ValueError(
            f"The subnets a {config.vpc_cidr} lays out are not free in {vpc_id}: "
            + "; ".join(taken)
            + ". Give a range whose addresses none of their subnets use."
        )


def create(
    name: str,
    config: AWSConfig,
    vpc_id: pulumi.Input[str],
    az: str,
    index: int,
    *,
    is_public: bool,
    opts: pulumi.ResourceOptions,
    map_public_ip: bool | None = None,
) -> aws.ec2.Subnet:
    kind = "public" if is_public else "private"
    role = "elb" if is_public else "internal-elb"
    return aws.ec2.Subnet(
        f"{name}-{kind}-{az}",
        vpc_id=vpc_id,
        cidr_block=str(cidr(config.vpc_cidr, index, is_public=is_public)),
        availability_zone=az,
        map_public_ip_on_launch=map_public_ip,
        tags=config.tags(
            Name=f"{config.resource_prefix}-{kind}-{az}",
            **{f"kubernetes.io/role/{role}": "1"},
        ),
        opts=opts,
    )
