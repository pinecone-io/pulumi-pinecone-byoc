"""Pinecone BYOC deployment on Azure."""

import pulumi
from pulumi_pinecone_byoc.azure import PineconeAzureCluster, PineconeAzureClusterArgs

config = pulumi.Config()

cluster = PineconeAzureCluster(
    "pinecone-byoc",
    PineconeAzureClusterArgs(
        pinecone_api_key=config.require_secret("pinecone-api-key"),
        pinecone_version=config.require("pinecone-version"),
        subscription_id=config.require("subscription-id"),
        region=config.require("region"),
        availability_zones=config.require_object("availability-zones"),
        vpc_cidr=config.get("vpc-cidr") or "10.0.0.0/16",
        deletion_protection=config.get_bool("deletion-protection") if config.get_bool("deletion-protection") is not None else True,
        public_access_enabled=config.get_bool("public-access-enabled") if config.get_bool("public-access-enabled") is not None else True,
        tags=config.get_object("tags"),
        global_env=config.require("global-env"),
        api_url=config.require("api-url"),
        auth0_domain=config.require("auth0-domain"),
        amp_aws_account_id=config.get("amp-aws-account-id") or "115740606080",
        gcp_project=config.get("gcp-project") or "development-pinecone",
    ),
)

region = config.require("region")
update_kubeconfig_command = cluster.name.apply(
    lambda name: f"az aks get-credentials --resource-group {name.removeprefix('cluster-')}-{region}-rg --name {name}"
)
pulumi.export("environment", cluster.environment.env_name)
pulumi.export("update_kubeconfig_command", update_kubeconfig_command)
