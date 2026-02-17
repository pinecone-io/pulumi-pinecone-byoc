"""PineconeAzureCluster - main component for BYOC deployments on Azure."""

from dataclasses import dataclass, field

import pulumi
import pulumi_azure_native as azure_native

from ..common.cred_refresher import RegistryCredentialRefresher
from ..common.k8s_configmaps import K8sConfigMaps
from ..common.k8s_secrets import K8sSecrets
from ..common.naming import cell_name as _cell_name
from ..common.pinetools import Pinetools
from ..common.providers import (
    AmpAccess,
    AmpAccessArgs,
    ApiKey,
    ApiKeyArgs,
    CpgwApiKey,
    CpgwApiKeyArgs,
    DatadogApiKey,
    DatadogApiKeyArgs,
    Environment,
    EnvironmentArgs,
    ServiceAccount,
    ServiceAccountArgs,
)
from ..common.registry import AZURE_REGISTRY
from ..common.uninstaller import ClusterUninstaller
from .aks import AKS
from .database import Database
from .dns import DNS
from .k8s_addons import K8sAddons
from .nlb import InternalLoadBalancer
from .pulumi_operator import PulumiOperator
from .storage import BlobStorage
from .vnet import VNet


@dataclass
class NodePool:
    name: str
    vm_size: str = "Standard_D4s_v3"
    min_size: int = 1
    max_size: int = 10
    disk_size_gb: int = 100
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)


@dataclass
class PineconeAzureClusterArgs:
    # required
    pinecone_api_key: pulumi.Input[str]
    pinecone_version: str

    # azure specific
    subscription_id: str = ""
    region: str = "eastus"
    availability_zones: list[str] = field(default_factory=lambda: ["1", "2"])

    # networking
    vpc_cidr: str = "10.0.0.0/16"

    # kubernetes
    kubernetes_version: str = "1.33"
    node_pools: list[NodePool] | None = None

    # dns
    parent_dns_zone_name: str = "byoc.pinecone.io"

    # features
    public_access_enabled: bool = True
    deletion_protection: bool = True

    # pinecone specific
    api_url: str = "https://api.pinecone.io"
    global_env: str = "prod"
    auth0_domain: str = "https://login.pinecone.io"

    # cross-cloud: AWS account for AMP federation (empty = skip AMP setup)
    amp_aws_account_id: str = ""

    # cross-cloud: gcp project for helmfile templates
    gcp_project: str = "production-pinecone"

    # tags
    tags: dict[str, str] | None = None


class PineconeAzureCluster(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        args: PineconeAzureClusterArgs,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:PineconeAzureCluster", name, None, opts)

        self.args = args
        child_opts = pulumi.ResourceOptions(parent=self)
        config = self._build_config(args)
        self._config = config

        client_config = azure_native.authorization.get_client_config()
        tenant_id = client_config.tenant_id

        # phase 1: authentication
        self._environment = Environment(
            f"{config.resource_prefix}-environment",
            EnvironmentArgs(
                cloud="azure",
                region=args.region,
                global_env=args.global_env,
                api_url=args.api_url,
                secret=args.pinecone_api_key,
            ),
            opts=child_opts,
        )

        self._cell_name = _cell_name(self._environment)
        self._resource_suffix = self._cell_name.apply(lambda cn: cn[-4:])

        self._cpgw_api_key = CpgwApiKey(
            f"{config.resource_prefix}-cpgw-api-key",
            CpgwApiKeyArgs(
                environment=self._environment.env_name,
                api_url=args.api_url,
                pinecone_api_key=args.pinecone_api_key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._environment]),
        )

        self._service_account = ServiceAccount(
            f"{config.resource_prefix}-service-account",
            ServiceAccountArgs(
                name=self._cell_name.apply(lambda cn: f"{cn}-sa"),
                api_url=args.api_url,
                secret=self._cpgw_api_key.key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
        )

        self._api_key = ApiKey(
            f"{config.resource_prefix}-api-key",
            ApiKeyArgs(
                org_id=self._environment.org_id,
                project_name="__SLI__",
                key_name=self._cell_name.apply(lambda cn: f"{cn}-key"),
                api_url=args.api_url,
                auth0_domain=args.auth0_domain,
                auth0_client_id=self._service_account.client_id,
                auth0_client_secret=self._service_account.client_secret,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._service_account]),
        )

        self._datadog_api_key = DatadogApiKey(
            f"{config.resource_prefix}-datadog-api-key",
            DatadogApiKeyArgs(
                api_url=args.api_url,
                cpgw_api_key=self._cpgw_api_key.key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
        )

        # phase 2: infrastructure
        self._vnet = VNet(
            f"{config.resource_prefix}-vnet",
            config,
            self._cell_name,
            opts=child_opts,
        )

        self._aks = AKS(
            f"{config.resource_prefix}-aks",
            config,
            resource_group_name=self._vnet.resource_group_name,
            subnet_id=self._vnet.aks_subnet_id,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vnet]),
        )

        self._storage = BlobStorage(
            f"{config.resource_prefix}-storage",
            config,
            cell_name=self._cell_name,
            resource_group_name=self._vnet.resource_group_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._aks]),
        )

        self._database = Database(
            f"{config.resource_prefix}-database",
            config,
            resource_group_name=self._vnet.resource_group_name,
            vnet_id=self._vnet.vnet_id,
            delegated_subnet_id=self._vnet.db_subnet_id,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vnet]),
        )

        # phase 3: dns & networking
        self._subdomain = self._environment.env_name

        self._dns = DNS(
            f"{config.resource_prefix}-dns",
            subdomain=self._subdomain.apply(lambda name: name.removesuffix(".byoc")),
            parent_zone_name=args.parent_dns_zone_name,
            api_url=args.api_url,
            cpgw_api_key=self._cpgw_api_key.key,
            cell_name=self._cell_name,
            resource_group_name=self._vnet.resource_group_name,
            location=config.region,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
        )

        self._k8s_addons = K8sAddons(
            f"{config.resource_prefix}-k8s-addons",
            config,
            k8s_provider=self._aks.k8s_provider,
            oidc_issuer_url=self._aks.oidc_issuer_url,
            resource_group_name=self._vnet.resource_group_name,
            dns_zone_id=self._dns.zone.id,
            cell_name=self._cell_name,
            tenant_id=tenant_id,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._aks]),
        )

        self._nlb = InternalLoadBalancer(
            f"{config.resource_prefix}-nlb",
            config=config,
            k8s_provider=self._aks.k8s_provider,
            resource_group_name=self._vnet.resource_group_name,
            pls_subnet_name=self._vnet.pls_subnet_name,
            dns_zone_name=self._dns.zone.name,
            subdomain=self._dns.subdomain,
            external_ip_address=self._dns.external_ip.ip_address.apply(lambda ip: ip or ""),
            cell_name=self._cell_name,
            public_access_enabled=args.public_access_enabled,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._vnet, self._dns, self._aks, self._k8s_addons],
            ),
        )

        # phase 4: k8s configuration
        self._k8s_secrets = K8sSecrets(
            f"{config.resource_prefix}-k8s-secrets",
            k8s_provider=self._aks.k8s_provider,
            cpgw_api_key=self._cpgw_api_key.key,
            gcps_api_key=self._api_key.value,
            dd_api_key=self._datadog_api_key.api_key,
            control_db=self._database.control_db,
            system_db=self._database.system_db,
            azure_storage_access_key=self._storage.access_key,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[
                    self._aks,
                    self._cpgw_api_key,
                    self._api_key,
                    self._datadog_api_key,
                    self._database,
                ],
            ),
        )

        self._pulumi_operator = PulumiOperator(
            f"{config.resource_prefix}-pulumi-operator",
            config,
            k8s_provider=self._aks.k8s_provider,
            resource_group_name=self._vnet.resource_group_name,
            resource_group_id=self._vnet.resource_group_id,
            oidc_issuer_url=self._aks.oidc_issuer_url,
            tenant_id=tenant_id,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._aks]),
        )

        # AMP: CPGW acts as credential broker for Azure (no direct OIDC federation)
        # the per-customer role trusts the CPGW IAM user, which assumes it via STS
        self._amp_access = None
        if args.amp_aws_account_id:
            self._amp_access = AmpAccess(
                f"{config.resource_prefix}-amp-access",
                AmpAccessArgs(
                    workload_role_arn=f"arn:aws:iam::{args.amp_aws_account_id}:user/AmpCpgwIamManagerUser",
                    api_url=args.api_url,
                    cpgw_api_key=self._cpgw_api_key.key,
                ),
                opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
            )

        pulumi_outputs = {
            "cell_name": self._cell_name,
            "org_name": self._environment.org_name,
            "cloud": "azure",
            "region": config.region,
            "global_env": config.global_env,
            "subdomain": self._subdomain,
            "availability_zones": config.availability_zones,
            "dns_zone_name": self._dns.zone.name,
            "azure_k8s_version": args.kubernetes_version,
            "azure_subscription_id": config.subscription_id,
            "azure_subnet_id": self._vnet.aks_subnet_id,
            "azure_client_id": self._k8s_addons.dns_identity_client_id,
            "azure_certmanager_client_id": self._k8s_addons.certmanager_identity_client_id,
            "azure_tenant_id": tenant_id,
            "azure_resource_group": self._vnet.resource_group.name,
            "azure_pulumi_operator_client_id": self._pulumi_operator.identity_client_id,
            "data_storage_account_name": self._storage.account_name,
            "image_registry": AZURE_REGISTRY.base_url,
            "sli_checkers_project_id": self._api_key.project_id,
            "gcp_project": args.gcp_project,
            "cpgw_api_key": self._cpgw_api_key.key,
            "cpgw_admin_api_key_id": self._cpgw_api_key.key_id,
            "api_url": args.api_url,
            "auth0_domain": args.auth0_domain,
            "customer_tags": args.tags or {},
            "public_access_enabled": args.public_access_enabled,
            "pulumi_backend_url": self._pulumi_operator.backend_url,
            "pulumi_secrets_provider": self._pulumi_operator.secrets_provider,
            "aws_amp_region": self._amp_access.amp_region if self._amp_access else "",
            "aws_amp_remote_write_url": self._amp_access.amp_remote_write_endpoint
            if self._amp_access
            else "",
            "aws_amp_sigv4_role_arn": self._amp_access.pinecone_role_arn
            if self._amp_access
            else "",
            "aws_amp_ingest_role_arn": "",
        }

        self._k8s_configmaps = K8sConfigMaps(
            f"{config.resource_prefix}-k8s-configmaps",
            k8s_provider=self._aks.k8s_provider,
            cloud="azure",
            cell_name=self._cell_name,
            env=config.global_env,
            is_prod=config.global_env == "prod",
            domain=self._subdomain,
            region=config.region,
            public_access_enabled=args.public_access_enabled,
            pulumi_outputs=pulumi_outputs,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._aks, self._dns, self._storage, self._database],
            ),
        )

        self._acr_refresher = RegistryCredentialRefresher(
            f"{config.resource_prefix}-acr-refresher",
            k8s_provider=self._aks.k8s_provider,
            cpgw_url=args.api_url,
            registry=AZURE_REGISTRY.type,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._k8s_secrets]),
        )

        self._pinetools = Pinetools(
            f"{config.resource_prefix}-pinetools",
            k8s_provider=self._aks.k8s_provider,
            pinecone_version=args.pinecone_version,
            pinetools_image=AZURE_REGISTRY.pinetools_image,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._aks, self._k8s_configmaps]),
        )

        # phase 5: cleanup
        self._uninstaller = ClusterUninstaller(
            f"{config.resource_prefix}-uninstaller",
            kubeconfig=self._aks.kubeconfig,
            pinetools_image=self._pinetools.pinetools_image,
            cloud="azure",
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[
                    self._pinetools,
                    self._k8s_addons,
                    self._k8s_secrets,
                    self._k8s_configmaps,
                    self._acr_refresher,
                    self._nlb,
                    self._pulumi_operator,
                ],
            ),
        )

        self.register_outputs(
            {
                "cluster_name": self._cell_name,
                "region": config.region,
                "organization_id": self._environment.org_id,
                "organization_name": self._environment.org_name,
                "vnet_id": self._vnet.vnet_id,
                "kubeconfig": self._aks.kubeconfig,
                "storage_account_name": self._storage.account_name,
                "control_db_endpoint": self._database.control_db.endpoint,
                "system_db_endpoint": self._database.system_db.endpoint,
                "environment_id": self._environment.id,
                "environment_name": self._environment.env_name,
                "service_account_id": self._service_account.id,
                "service_account_client_id": self._service_account.client_id,
                "api_key_project_id": self._api_key.project_id,
                "subdomain": self._subdomain,
                "sli_checkers_project_id": self._api_key.project_id,
                "cpgw_api_key": self._k8s_secrets.cpgw_api_key,
                "cpgw_admin_api_key_id": self._cpgw_api_key.key_id,
                "datadog_api_key_id": self._datadog_api_key.key_id,
                "customer_tags": config.custom_tags,
                "pulumi_backend_url": self._pulumi_operator.backend_url,
                "pulumi_secrets_provider": self._pulumi_operator.secrets_provider,
            }
        )

    def _build_config(self, args: PineconeAzureClusterArgs):
        from config.azure import AzureConfig, FlexibleServerConfig
        from config.base import NodePoolConfig

        node_pools = []
        if args.node_pools:
            for np in args.node_pools:
                node_pools.append(
                    NodePoolConfig(
                        name=np.name,
                        vm_size=np.vm_size,
                        min_size=np.min_size,
                        max_size=np.max_size,
                        disk_size_gb=np.disk_size_gb,
                        labels=np.labels,
                        taints=np.taints,
                    )
                )
        else:
            node_pools = [
                NodePoolConfig(
                    name="default",
                    vm_size="Standard_D4s_v5",
                    min_size=1,
                    max_size=10,
                    disk_size_gb=100,
                ),
            ]

        return AzureConfig(
            region=args.region,
            global_env=args.global_env,
            cloud="azure",
            subscription_id=args.subscription_id,
            availability_zones=args.availability_zones,
            vpc_cidr=args.vpc_cidr,
            kubernetes_version=args.kubernetes_version,
            node_pools=node_pools,
            parent_zone_name=args.parent_dns_zone_name,
            database=FlexibleServerConfig(
                deletion_protection=args.deletion_protection,
            ),
            custom_tags=args.tags or {},
        )

    @property
    def environment(self) -> Environment:
        return self._environment

    @property
    def name(self) -> pulumi.Output[str]:
        return self._aks.cluster.name

    @property
    def vnet(self) -> VNet:
        return self._vnet

    @property
    def aks(self) -> AKS:
        return self._aks

    @property
    def storage(self) -> BlobStorage:
        return self._storage

    @property
    def database(self) -> Database:
        return self._database

    @property
    def dns(self) -> DNS:
        return self._dns
