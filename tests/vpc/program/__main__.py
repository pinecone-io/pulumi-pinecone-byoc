import pulumi

from pulumi_pinecone_byoc.aws import PineconeAWSCluster, PineconeAWSClusterArgs

config = pulumi.Config()

cluster = PineconeAWSCluster(
    name="pinecone-aws-cluster",
    args=PineconeAWSClusterArgs(
        pinecone_api_key="the network needs no key, and network_only asks for none",
        pinecone_version="unused",
        region=config.require("region"),
        vpc_cidr=config.require("vpc-cidr"),
        availability_zones=config.require_object("availability-zones"),
        existing_vpc_id=config.get("existing-vpc-id"),
        public_access_enabled=config.get_bool("public-access-enabled") is not False,
        network_only=True,
    ),
)

pulumi.export("vpc_id", cluster.vpc_id)
pulumi.export("private_subnet_ids", cluster.private_subnet_ids)
pulumi.export("public_subnet_ids", cluster.public_subnet_ids)
