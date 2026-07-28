import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws.vpc import VPC

MODES = ("public",)

config = pulumi.Config()
stack = pulumi.get_stack()
tokens = stack.split("-")
mode = config.get("mode") or next((m for m in MODES if m in tokens), "")
if mode not in MODES:
    raise ValueError(
        f"cannot determine mode for stack {stack!r}: include one of "
        f"{'/'.join(MODES)} in the stack name (e.g. ilia-public or public-ci) "
        "or set byovpc:mode explicitly"
    )

name = config.get("name") or f"byovpc-{stack}"
vpc_cidr = config.get("vpc-cidr") or "10.0.0.0/16"
azs = config.get_object("azs") or ["us-east-2a", "us-east-2b"]

network = ipaddress.IPv4Network(vpc_cidr)
if network.prefixlen != 16:
    raise ValueError(f"vpc-cidr must be a /16 (got /{network.prefixlen})")

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


def joined(ids):
    if not ids:
        return pulumi.Output.from_input("")
    return pulumi.Output.all(*ids).apply(lambda v: ",".join(v))


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

# egress lives on the main route table so subnets that carry no route table of
# their own still reach the internet
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

wizard_env = pulumi.Output.all(vpc_id, public_ids, private_ids).apply(
    lambda a: "\n".join(
        [
            f"export PINECONE_REGION={region}",
            f"export PINECONE_AZS={','.join(azs)}",
            f"export PINECONE_EXISTING_VPC_ID={a[0]}",
            f"export PINECONE_PUBLIC_SUBNET_IDS={a[1]}",
            f"export PINECONE_PRIVATE_SUBNET_IDS={a[2]}",
            "export PINECONE_PUBLIC_ACCESS=true",
            f"export PINECONE_VPC_CIDR={vpc_cidr}",
        ]
    )
)

pulumi.export("wizard_env", wizard_env)
