import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws.vpc import VPC

MODES = ("public", "carve")

config = pulumi.Config()
stack = pulumi.get_stack()
tokens = stack.split("-")
mode = config.get("mode") or next((m for m in MODES if m in tokens), "")
if mode not in MODES:
    raise ValueError(
        f"cannot determine mode for stack {stack!r}: include one of "
        f"{'/'.join(MODES)} in the stack name (e.g. ilia-carve or carve-ci) "
        "or set byovpc:mode explicitly"
    )

name = config.get("name") or f"byovpc-{stack}"
vpc_cidr = config.get("vpc-cidr") or "10.0.0.0/16"
azs = config.get_object("azs") or ["us-east-2a", "us-east-2b"]

network = ipaddress.IPv4Network(vpc_cidr)
if network.prefixlen != 16:
    raise ValueError(f"vpc-cidr must be a /16 (got /{network.prefixlen})")
octets = str(network.network_address).split(".")

region = azs[0][:-1]
provider_region = pulumi.Config("aws").get("region")
if not provider_region:
    raise ValueError(
        f"aws:region is not set for stack {stack!r}: run "
        f"`pulumi config set aws:region {region}` so the provider cannot fall back "
        "to a different region than the configured azs"
    )
if provider_region != region:
    raise ValueError(
        f"aws:region is {provider_region!r} but azs are in {region!r}: set both to the same region"
    )
if any(az[:-1] != region for az in azs):
    raise ValueError(f"all azs must be in one region, got {azs}")

base_tags = {"pinecone-byoc-test": name}


def public_cidr(index: int) -> str:
    return f"{octets[0]}.{octets[1]}.{index * 16}.0/20"


def carve_cidr() -> str:
    return f"{octets[0]}.{int(octets[1]) + 1}.0.0/16"


def joined(ids):
    if not ids:
        return pulumi.Output.from_input("")
    return pulumi.Output.all(*ids).apply(lambda v: ",".join(v))


if mode == "public":
    byoc = VPC(
        name,
        AWSConfig(
            region=region,
            availability_zones=azs,
            vpc_cidr=vpc_cidr,
            custom_tags=base_tags,
        ),
    )
    vpc_id = byoc.vpc_id
    default_route_table_id = byoc.vpc.default_route_table_id
    egress_nat_id = byoc.nat_gateways[0].id
    public_ids = joined(byoc.public_subnet_ids)
    private_ids = joined(byoc.private_subnet_ids)
else:
    vpc = aws.ec2.Vpc(
        name,
        cidr_block=vpc_cidr,
        enable_dns_support=True,
        enable_dns_hostnames=True,
        tags={**base_tags, "Name": name},
    )

    igw = aws.ec2.InternetGateway(
        f"{name}-igw",
        vpc_id=vpc.id,
        tags={**base_tags, "Name": f"{name}-igw"},
    )

    igw_rt = aws.ec2.RouteTable(
        f"{name}-igw-rt",
        vpc_id=vpc.id,
        tags={**base_tags, "Name": f"{name}-igw-rt"},
    )

    aws.ec2.Route(
        f"{name}-igw-route",
        route_table_id=igw_rt.id,
        destination_cidr_block="0.0.0.0/0",
        gateway_id=igw.id,
    )

    nat_subnet = aws.ec2.Subnet(
        f"{name}-nat-subnet",
        vpc_id=vpc.id,
        cidr_block=public_cidr(0),
        availability_zone=azs[0],
        tags={**base_tags, "Name": f"{name}-nat-subnet"},
    )

    aws.ec2.RouteTableAssociation(
        f"{name}-nat-rta",
        subnet_id=nat_subnet.id,
        route_table_id=igw_rt.id,
    )

    eip = aws.ec2.Eip(
        f"{name}-nat-eip",
        domain="vpc",
        tags={**base_tags, "Name": f"{name}-nat-eip"},
    )

    nat = aws.ec2.NatGateway(
        f"{name}-nat",
        allocation_id=eip.id,
        subnet_id=nat_subnet.id,
        tags={**base_tags, "Name": f"{name}-nat"},
        opts=pulumi.ResourceOptions(depends_on=[igw]),
    )

    vpc_id = vpc.id
    default_route_table_id = vpc.default_route_table_id
    egress_nat_id = nat.id
    public_ids = pulumi.Output.from_input("")
    private_ids = pulumi.Output.from_input("")

# egress lives on the main route table so the subnets the module carves inherit it
main_rt = aws.ec2.DefaultRouteTable(
    f"{name}-main-rt",
    default_route_table_id=default_route_table_id,
    routes=[
        aws.ec2.DefaultRouteTableRouteArgs(
            cidr_block="0.0.0.0/0",
            nat_gateway_id=egress_nat_id,
        )
    ],
    tags={**base_tags, "Name": f"{name}-main-rt"},
)

pulumi.export("mode", mode)
pulumi.export("vpc_id", vpc_id)
pulumi.export("vpc_cidr", vpc_cidr)
pulumi.export("azs", ",".join(azs))
pulumi.export("public_subnet_ids", public_ids)
pulumi.export("private_subnet_ids", private_ids)
pulumi.export("main_route_table_id", default_route_table_id)
pulumi.export("carve_cidr", carve_cidr())

wizard_env = pulumi.Output.all(vpc_id, public_ids, private_ids).apply(
    lambda a: "\n".join(
        [
            f"export PINECONE_REGION={region}",
            f"export PINECONE_AZS={','.join(azs)}",
            f"export PINECONE_EXISTING_VPC_ID={a[0]}",
            f"export PINECONE_PUBLIC_SUBNET_IDS={a[1]}",
            f"export PINECONE_PRIVATE_SUBNET_IDS={a[2]}",
            f"export PINECONE_PUBLIC_ACCESS={'true' if mode == 'public' else 'false'}",
            f"export PINECONE_VPC_CIDR={carve_cidr() if mode == 'carve' else vpc_cidr}",
        ]
    )
)

pulumi.export("wizard_env", wizard_env)
