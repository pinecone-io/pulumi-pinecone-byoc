"""
pulumi-pinecone-byoc - Pulumi components for Pinecone BYOC clusters.
"""

__version__ = "0.1.0"

# primary exports - what most customers need
from .cluster import PineconeAWSCluster, PineconeAWSClusterArgs, NodePool

# individual components for advanced usage
from .vpc import VPC
from .eks import EKS
from .s3 import S3Buckets
from .dns import DNS
from .nlb import NLB
from .rds import RDS, RDSInstance
from .k8s_addons import K8sAddons
from .k8s_secrets import K8sSecrets
from .k8s_configmaps import K8sConfigMaps
from .ecr_refresher import EcrCredentialRefresher
from .pulumi_operator import PulumiOperator
from .pinetools import Pinetools

# low-level providers (rarely needed directly)
from .providers import (
    Environment,
    EnvironmentArgs,
    ServiceAccount,
    ServiceAccountArgs,
    ApiKey,
    ApiKeyArgs,
    DnsDelegation,
    DnsDelegationArgs,
    DatadogApiKey,
    DatadogApiKeyArgs,
)

__all__ = [
    # primary
    "PineconeAWSCluster",
    "PineconeAWSClusterArgs",
    "NodePool",
    # components
    "VPC",
    "EKS",
    "S3Buckets",
    "DNS",
    "NLB",
    "RDS",
    "RDSInstance",
    "K8sAddons",
    "K8sSecrets",
    "K8sConfigMaps",
    "EcrCredentialRefresher",
    "PulumiOperator",
    "Pinetools",
    # providers
    "Environment",
    "EnvironmentArgs",
    "ServiceAccount",
    "ServiceAccountArgs",
    "ApiKey",
    "ApiKeyArgs",
    "DnsDelegation",
    "DnsDelegationArgs",
    "DatadogApiKey",
    "DatadogApiKeyArgs",
]
