"""
PineconeAWSCluster - main component for BYOC deployments.
"""

from typing import Optional
from dataclasses import dataclass, field
import json

import pulumi
import pulumi_aws as aws

from config import sanitize
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
from .providers import (
    Environment,
    EnvironmentArgs,
    ServiceAccount,
    ServiceAccountArgs,
    ApiKey,
    ApiKeyArgs,
    CpgwApiKey,
    CpgwApiKeyArgs,
    DatadogApiKey,
    DatadogApiKeyArgs,
    AmpAccess,
    AmpAccessArgs,
)


@dataclass
class NodePool:
    name: str
    instance_type: str = "m6i.xlarge"
    min_size: int = 1
    max_size: int = 10
    desired_size: int = 3
    disk_size_gb: int = 100
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)


@dataclass
class PineconeAWSClusterArgs:
    # required
    pinecone_api_key: pulumi.Input[str]

    # aws specific
    region: pulumi.Input[str] = "us-east-1"
    availability_zones: pulumi.Input[list[str]] = field(
        default_factory=lambda: ["us-east-1a", "us-east-1b"]
    )

    # networking
    vpc_cidr: pulumi.Input[str] = "10.0.0.0/16"

    # kubernetes
    kubernetes_version: pulumi.Input[str] = "1.33"
    node_pools: Optional[list[NodePool]] = None

    # dns
    parent_dns_zone_name: pulumi.Input[str] = "byoc.pinecone.io"

    # features
    public_access_enabled: bool = True  # false = private access only via privatelink
    deletion_protection: bool = True  # protect RDS and S3 from accidental deletion

    # pinecone specific
    api_url: pulumi.Input[str] = "https://api.pinecone.io"
    global_env: pulumi.Input[str] = "prod"
    auth0_domain: pulumi.Input[str] = "https://login.pinecone.io"
    gcp_project: Optional[pulumi.Input[str]] = None  # defaults based on global_env
    pinecone_version: pulumi.Input[str] = "main-3d6741d"

    # tags
    tags: Optional[dict[str, str]] = None


class PineconeAWSCluster(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        args: PineconeAWSClusterArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:PineconeAWSCluster", name, None, opts)

        self.args = args
        child_opts = pulumi.ResourceOptions(parent=self)
        config = self._build_config(args)
        self._config = config

        self._environment = Environment(
            f"{config.resource_prefix}-environment",
            EnvironmentArgs(
                cloud="aws",
                region=args.region,
                global_env=args.global_env,
                api_url=args.api_url,
                secret=args.pinecone_api_key,
            ),
            opts=child_opts,
        )

        # cell_name derived from org_name (from environment response)
        self._cell_name = self._environment.org_name.apply(
            lambda org_name: f"{args.global_env}-aws-{sanitize(org_name)}"
        )

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
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self._service_account]
            ),
        )

        self._datadog_api_key = DatadogApiKey(
            f"{config.resource_prefix}-datadog-api-key",
            DatadogApiKeyArgs(
                api_url=args.api_url,
                cpgw_api_key=self._cpgw_api_key.key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
        )

        self._vpc = VPC(f"{config.resource_prefix}-vpc", config, opts=child_opts)

        self._eks = EKS(
            f"{config.resource_prefix}-eks",
            config,
            self._vpc,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vpc]),
        )

        self._s3 = S3Buckets(
            f"{config.resource_prefix}-s3",
            config,
            cell_name=self._cell_name,
            force_destroy=not args.deletion_protection,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        # storage integration role - allows data-importer to access customer S3 data
        # trust policy allows any role in the account (ec2 node role can assume it)
        caller_identity = aws.get_caller_identity()
        assume_role_policy = pulumi.Output.from_input(caller_identity.account_id).apply(
            lambda account_id: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": {
                        "Sid": "AllowAccountRoles",
                        "Effect": "Allow",
                        "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                        "Action": "sts:AssumeRole",
                    },
                }
            )
        )
        self._storage_integration_role = aws.iam.Role(
            f"{config.resource_prefix}-storage-integration-role",
            name=f"{config.resource_prefix}-storage-integration",
            assume_role_policy=assume_role_policy,
            tags=config.tags(Name=f"{config.resource_prefix}-storage-integration"),
            opts=child_opts,
        )
        aws.iam.RolePolicyAttachment(
            f"{config.resource_prefix}-storage-integration-policy",
            role=self._storage_integration_role.id,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            opts=child_opts,
        )
        # allow ec2 node role to assume storage integration role (for data-importer)
        aws.iam.RolePolicy(
            f"{config.resource_prefix}-ec2-allow-assume-role",
            role=self._eks.node_role_name,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "sts:AssumeRole",
                            "Resource": "arn:aws:iam::*:role/*",
                        },
                    ],
                }
            ),
            opts=child_opts,
        )

        self._subdomain = self._environment.env_name

        self._dns = DNS(
            f"{config.resource_prefix}-dns",
            subdomain=self._subdomain.apply(lambda name: name.removesuffix(".byoc")),
            parent_zone_name=args.parent_dns_zone_name,
            api_url=args.api_url,
            cpgw_api_key=self._cpgw_api_key.key,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._cpgw_api_key]),
        )

        self._rds = RDS(
            f"{config.resource_prefix}-rds",
            config,
            self._vpc,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vpc]),
        )

        self._k8s_addons = K8sAddons(
            f"{config.resource_prefix}-k8s-addons",
            config,
            self._eks,
            self._vpc.vpc_id,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        # AMP access for Prometheus remote write
        self._amp_access = AmpAccess(
            f"{config.resource_prefix}-amp-access",
            AmpAccessArgs(
                workload_role_arn=self._k8s_addons.amp_ingest_role.arn,
                api_url=args.api_url,
                cpgw_api_key=self._cpgw_api_key.key,
            ),
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self._cpgw_api_key, self._k8s_addons]
            ),
        )

        # Allow the AMP ingest role to assume the Pinecone role
        aws.iam.RolePolicy(
            f"{config.resource_prefix}-amp-allow-assume-pinecone-role",
            role=self._k8s_addons.amp_ingest_role.id,
            policy=self._amp_access.pinecone_role_arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "sts:AssumeRole",
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._amp_access]),
        )

        # NLB for private endpoint access (creates private ALB + NLB)
        # must be after K8sAddons so ALB Controller is available
        self._nlb = NLB(
            f"{config.resource_prefix}-nlb",
            config,
            self._vpc,
            self._dns,
            k8s_provider=self._eks.provider,
            cluster_security_group_id=self._eks.cluster_security_group_id,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._vpc, self._dns, self._eks, self._k8s_addons],
            ),
        )

        self._k8s_secrets = K8sSecrets(
            f"{config.resource_prefix}-k8s-secrets",
            k8s_provider=self._eks.provider,
            cpgw_api_key=self._cpgw_api_key.key,
            gcps_api_key=self._api_key.value,
            dd_api_key=self._datadog_api_key.api_key,
            control_db=self._rds.control_db,
            system_db=self._rds.system_db,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._eks, self._api_key, self._datadog_api_key, self._rds],
            ),
        )

        # pulumi-k8s-operator with s3 backend (no pulumi cloud token needed)
        # create before K8sConfigMaps so we can include backend_url and secrets_provider
        # note: the ServiceAccount is created by the Helm chart via helmfile config
        self._pulumi_operator = PulumiOperator(
            f"{config.resource_prefix}-pulumi-operator",
            config,
            oidc_provider_arn=self._eks.oidc_provider_arn,
            oidc_provider_url=self._eks.oidc_provider_url,
            cell_name=self._cell_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        # allow ec2 nodes to use pulumi kms key (for stack crds using node credentials)
        aws.iam.RolePolicy(
            f"{config.resource_prefix}-ec2-allow-kms",
            role=self._eks.node_role_name,
            policy=self._pulumi_operator.kms_key_arn.apply(
                lambda kms_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "kms:Encrypt",
                                    "kms:Decrypt",
                                    "kms:GenerateDataKey",
                                    "kms:DescribeKey",
                                ],
                                "Resource": kms_arn,
                            }
                        ],
                    }
                )
            ),
            opts=child_opts,
        )

        # gcp_project is needed by some helmfiles even for AWS clusters
        # default based on global_env if not explicitly set
        def get_gcp_project(env: str) -> str:
            if args.gcp_project:
                return args.gcp_project
            return "production-pinecone" if env == "prod" else "development-pinecone"

        pulumi_outputs = {
            "cell_name": self._cell_name,
            "cloud": "aws",
            "region": args.region,
            "global_env": args.global_env,
            "subdomain": self._subdomain,
            "availability_zones": args.availability_zones,
            "certificate_arn": self._dns.certificate_arn,
            "dns_zone_id": self._dns.zone_id,
            "private_endpoint_certificate_arn": self._dns.certificate_arn,
            "aws_k8s_version": args.kubernetes_version,
            "aws_ec2_iam_role_arn": self._eks.node_role_arn,
            "aws_subnet_ids": self._vpc.private_subnet_ids,
            "image_registry": "843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4",
            "gcp_project": pulumi.Output.from_input(args.global_env).apply(
                get_gcp_project
            ),
            "sli_checkers_project_id": self._api_key.project_id,
            "aws_storage_integration_role_arn": self._storage_integration_role.arn,
            "customer_tags": args.tags or {},
            # pulumi operator s3 backend config for Stack CRDs
            "pulumi_backend_url": self._pulumi_operator.backend_url,
            "pulumi_secrets_provider": self._pulumi_operator.secrets_provider,
            "pulumi_operator_role_arn": self._pulumi_operator.operator_role_arn,
            # AMP remote write config
            "aws_amp_region": self._amp_access.amp_region,
            "aws_amp_remote_write_url": self._amp_access.amp_remote_write_endpoint,
            "aws_amp_sigv4_role_arn": self._amp_access.pinecone_role_arn,
            "aws_amp_ingest_role_arn": self._k8s_addons.amp_ingest_role.arn,
        }

        self._k8s_configmaps = K8sConfigMaps(
            f"{config.resource_prefix}-k8s-configmaps",
            k8s_provider=self._eks.provider,
            cloud="aws",
            cell_name=self._cell_name,
            env=args.global_env,
            is_prod=args.global_env == "prod",
            domain=self._subdomain,
            region=args.region,
            public_access_enabled=args.public_access_enabled,
            pulumi_outputs=pulumi_outputs,
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self._eks, self._dns, self._s3, self._rds]
            ),
        )

        # ecr credential refresher - distributes regcred to all pc-* namespaces
        # uses cpgw-credentials secret created by K8sSecrets
        self._ecr_refresher = EcrCredentialRefresher(
            f"{config.resource_prefix}-ecr-refresher",
            k8s_provider=self._eks.provider,
            cpgw_url=args.api_url,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._k8s_secrets]),
        )

        # pinetools for cluster management
        # depends on k8s_configmaps because pinetools needs the config configmap
        self._pinetools = Pinetools(
            f"{config.resource_prefix}-pinetools",
            k8s_provider=self._eks.provider,
            install_image_tag=args.pinecone_version,
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self._eks, self._k8s_configmaps]
            ),
        )

        self.register_outputs(
            {
                "cluster_name": self._cell_name,
                "region": args.region,
                "organization_id": self._environment.org_id,
                "organization_name": self._environment.org_name,
                "vpc_id": self._vpc.vpc_id,
                "cluster_endpoint": self._eks.cluster.eks_cluster.endpoint,
                "kubeconfig": self._eks.kubeconfig,
                "data_bucket": self._s3.data_bucket_name,
                "control_db_endpoint": self._rds.control_db.endpoint,
                "system_db_endpoint": self._rds.system_db.endpoint,
                "certificate_arn": self._dns.certificate_arn,
                "environment_id": self._environment.id,
                "environment_name": self._environment.env_name,
                "service_account_id": self._service_account.id,
                "service_account_client_id": self._service_account.client_id,
                "api_key_project_id": self._api_key.project_id,
                "alb_controller_role_arn": self._k8s_addons.alb_controller_role.arn,
                "cluster_autoscaler_role_arn": self._k8s_addons.cluster_autoscaler_role.arn,
                "subdomain": self._subdomain,
                "sli_checkers_project_id": self._api_key.project_id,
                "cpgw_api_key": self._k8s_secrets.cpgw_api_key,
                "cpgw_admin_api_key_id": self._cpgw_api_key.key_id,
                "datadog_api_key_id": self._datadog_api_key.key_id,
                "customer_tags": args.tags or {},
                "pulumi_backend_url": self._pulumi_operator.backend_url,
                "pulumi_secrets_provider": self._pulumi_operator.secrets_provider,
                "storage_integration_role_arn": self._storage_integration_role.arn,
                "amp_region": self._amp_access.amp_region,
                "amp_remote_write_endpoint": self._amp_access.amp_remote_write_endpoint,
                "amp_sigv4_role_arn": self._amp_access.pinecone_role_arn,
                "amp_ingest_role_arn": self._k8s_addons.amp_ingest_role.arn,
            }
        )

    def _build_config(self, args: PineconeAWSClusterArgs):
        from config import Config, NodePoolConfig, NodePoolTaint, DatabaseConfig

        node_pools = []
        if args.node_pools:
            for np in args.node_pools:
                node_pools.append(
                    NodePoolConfig(
                        name=np.name,
                        instance_type=np.instance_type,
                        min_size=np.min_size,
                        max_size=np.max_size,
                        desired_size=np.desired_size,
                        disk_size_gb=np.disk_size_gb,
                        labels=np.labels,
                        taints=[
                            NodePoolTaint(key=t.key, value=t.value, effect=t.effect)
                            for t in np.taints
                        ],
                    )
                )
        else:
            node_pools = [
                NodePoolConfig(
                    name="default",
                    instance_type="r6in.large",
                    min_size=1,
                    max_size=10,
                    desired_size=3,
                    disk_size_gb=100,
                ),
            ]

        return Config(
            region=args.region,
            environment="prod",
            global_env=args.global_env,
            cloud="aws",
            availability_zones=args.availability_zones,
            vpc_cidr=args.vpc_cidr,
            kubernetes_version=args.kubernetes_version,
            node_pools=node_pools,
            parent_zone_name=args.parent_dns_zone_name,
            database=DatabaseConfig(deletion_protection=args.deletion_protection),
        )

    @property
    def vpc_id(self) -> pulumi.Output[str]:
        return self._vpc.vpc_id

    @property
    def private_subnet_ids(self) -> list[pulumi.Output[str]]:
        return self._vpc.private_subnet_ids

    @property
    def public_subnet_ids(self) -> list[pulumi.Output[str]]:
        return self._vpc.public_subnet_ids

    @property
    def cluster_name(self) -> pulumi.Output[str]:
        return self._eks.cluster_name

    @property
    def cluster_endpoint(self) -> pulumi.Output[str]:
        return self._eks.cluster.eks_cluster.endpoint

    @property
    def kubeconfig(self) -> pulumi.Output:
        return self._eks.kubeconfig

    @property
    def k8s_provider(self) -> pulumi.ProviderResource:
        return self._eks.provider

    @property
    def oidc_provider_arn(self) -> pulumi.Output[str]:
        return self._eks.oidc_provider_arn

    @property
    def data_bucket_name(self) -> pulumi.Output[str]:
        return self._s3.data_bucket_name

    @property
    def data_bucket_arn(self) -> pulumi.Output[str]:
        return self._s3.data_bucket_arn

    @property
    def wal_bucket_name(self) -> pulumi.Output[str]:
        return self._s3.wal_bucket_name

    @property
    def control_db(self) -> RDSInstance:
        return self._rds.control_db

    @property
    def system_db(self) -> RDSInstance:
        return self._rds.system_db

    @property
    def control_db_endpoint(self) -> pulumi.Output[str]:
        return self._rds.control_db.endpoint

    @property
    def system_db_endpoint(self) -> pulumi.Output[str]:
        return self._rds.system_db.endpoint

    @property
    def control_db_connection_secret_arn(self) -> pulumi.Output[str]:
        return self._rds.control_db.connection_secret_arn

    @property
    def system_db_connection_secret_arn(self) -> pulumi.Output[str]:
        return self._rds.system_db.connection_secret_arn

    @property
    def certificate_arn(self) -> pulumi.Output[str]:
        return self._dns.certificate_arn

    @property
    def dns_zone_id(self) -> pulumi.Output[str]:
        return self._dns.zone_id

    @property
    def dns_name_servers(self) -> pulumi.Output[list]:
        return self._dns.name_servers

    @property
    def nlb_dns_name(self) -> Optional[pulumi.Output[str]]:
        return self._nlb.nlb_dns_name if self._nlb else None

    @property
    def nlb_target_group_arn(self) -> Optional[pulumi.Output[str]]:
        return self._nlb.target_group_arn if self._nlb else None

    @property
    def environment(self) -> Environment:
        return self._environment

    @property
    def environment_id(self) -> pulumi.Output[str]:
        return self._environment.id

    @property
    def environment_name(self) -> pulumi.Output[str]:
        return self._environment.env_name

    @property
    def service_account(self) -> ServiceAccount:
        return self._service_account

    @property
    def service_account_id(self) -> pulumi.Output[str]:
        return self._service_account.id

    @property
    def service_account_client_id(self) -> pulumi.Output[str]:
        return self._service_account.client_id

    @property
    def service_account_client_secret(self) -> pulumi.Output[str]:
        return self._service_account.client_secret

    @property
    def api_key(self) -> ApiKey:
        return self._api_key

    @property
    def api_key_value(self) -> pulumi.Output[str]:
        return self._api_key.value

    @property
    def api_key_project_id(self) -> pulumi.Output[str]:
        return self._api_key.project_id

    @property
    def subdomain(self) -> pulumi.Output[str]:
        return self._subdomain

    @property
    def sli_checkers_project_id(self) -> pulumi.Output[str]:
        return self._api_key.project_id

    @property
    def cpgw_api_key(self) -> pulumi.Output[str]:
        return self._k8s_secrets.cpgw_api_key

    @property
    def customer_tags(self) -> dict[str, str]:
        return self.args.tags or {}

    @property
    def datadog_api_key(self) -> DatadogApiKey:
        return self._datadog_api_key

    @property
    def datadog_api_key_value(self) -> pulumi.Output[str]:
        return self._datadog_api_key.api_key

    @property
    def datadog_api_key_id(self) -> pulumi.Output[str]:
        return self._datadog_api_key.key_id

    @property
    def cpgw_admin_api_key(self) -> CpgwApiKey:
        return self._cpgw_api_key

    @property
    def cpgw_admin_api_key_id(self) -> pulumi.Output[str]:
        return self._cpgw_api_key.key_id

    @property
    def cpgw_admin_api_key_value(self) -> pulumi.Output[str]:
        return self._cpgw_api_key.key

    @property
    def pulumi_operator(self) -> PulumiOperator:
        return self._pulumi_operator

    @property
    def pulumi_backend_url(self) -> pulumi.Output[str]:
        """S3 backend URL for Stack CRD spec.backend field."""
        return self._pulumi_operator.backend_url

    @property
    def pulumi_secrets_provider(self) -> pulumi.Output[str]:
        """KMS secrets provider for Stack CRD spec.secretsProvider field."""
        return self._pulumi_operator.secrets_provider

    @property
    def pulumi_operator_role_arn(self) -> pulumi.Output[str]:
        return self._pulumi_operator.operator_role_arn

    @property
    def amp_access(self) -> AmpAccess:
        return self._amp_access

    @property
    def amp_region(self) -> pulumi.Output[str]:
        return self._amp_access.amp_region

    @property
    def amp_remote_write_endpoint(self) -> pulumi.Output[str]:
        return self._amp_access.amp_remote_write_endpoint

    @property
    def amp_sigv4_role_arn(self) -> pulumi.Output[str]:
        return self._amp_access.pinecone_role_arn

    @property
    def amp_ingest_role_arn(self) -> pulumi.Output[str]:
        return self._k8s_addons.amp_ingest_role.arn
