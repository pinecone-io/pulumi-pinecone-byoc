"""Just the module's VPC component, for the network tier.

The wizard's program builds the whole cluster, and `pulumi up --target` on the
VPC subtree still constructs the rest: EKS and our own naming code then read
inputs the engine never resolved, because it was not asked to create what they
come from. Constructing only the component under test is the same code path for
the network, with nothing after it to go wrong.
"""

import pulumi

from config.aws import AWSConfig
from pulumi_pinecone_byoc.aws.vpc import VPC

config = pulumi.Config()

# the same default the generated cluster program applies, so a carve that asked for
# no ingress does not go looking for the customer's internet gateway
public_access = config.get_bool("public-access-enabled")

vpc = VPC(
    "pc-vpc",
    AWSConfig(
        region=config.require("region"),
        availability_zones=config.require_object("availability-zones"),
        vpc_cidr=config.require("vpc-cidr"),
        existing_vpc_id=config.get("existing-vpc-id"),
        public_access=True if public_access is None else public_access,
    ),
)

pulumi.export("vpc_id", vpc.vpc_id)
pulumi.export("public_subnet_ids", vpc.public_subnet_ids)
pulumi.export("private_subnet_ids", vpc.private_subnet_ids)
pulumi.export("vpc_cidr_blocks", vpc.vpc_cidr_blocks)
