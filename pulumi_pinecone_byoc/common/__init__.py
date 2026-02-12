"""Cloud-agnostic components for Pinecone BYOC deployment."""

from .api import (
    create_amp_access,
    create_api_key,
    create_cpgw_api_key,
    create_datadog_api_key,
    create_dns_delegation,
    create_environment,
    create_service_account,
)
from .cred_refresher import RegistryCredentialRefresher
from .k8s_configmaps import K8sConfigMaps
from .k8s_secrets import K8sSecrets
from .naming import DNS_CNAMES, cell_name
from .pinetools import Pinetools
from .providers import (
    AmpAccess,
    AmpAccessProvider,
    ApiKey,
    ApiKeyProvider,
    CpgwApiKey,
    CpgwApiKeyProvider,
    DatadogApiKey,
    DatadogApiKeyProvider,
    DnsDelegation,
    DnsDelegationProvider,
    Environment,
    EnvironmentProvider,
    ServiceAccount,
    ServiceAccountProvider,
)
from .registry import AWS_REGISTRY, GCP_REGISTRY, ContainerRegistry
from .uninstaller import ClusterUninstaller

__all__ = [
    "create_environment",
    "create_service_account",
    "create_api_key",
    "create_cpgw_api_key",
    "create_dns_delegation",
    "create_amp_access",
    "create_datadog_api_key",
    "Environment",
    "EnvironmentProvider",
    "ServiceAccount",
    "ServiceAccountProvider",
    "ApiKey",
    "ApiKeyProvider",
    "CpgwApiKey",
    "CpgwApiKeyProvider",
    "DnsDelegation",
    "DnsDelegationProvider",
    "AmpAccess",
    "AmpAccessProvider",
    "DatadogApiKey",
    "DatadogApiKeyProvider",
    "K8sConfigMaps",
    "K8sSecrets",
    "cell_name",
    "DNS_CNAMES",
    "RegistryCredentialRefresher",
    "Pinetools",
    "ClusterUninstaller",
    "ContainerRegistry",
    "AWS_REGISTRY",
    "GCP_REGISTRY",
]
