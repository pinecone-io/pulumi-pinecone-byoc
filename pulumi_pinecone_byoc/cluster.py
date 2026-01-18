"""
PineconeAWSCluster - main component for BYOC deployments.
"""

from typing import Optional
from dataclasses import dataclass, field
import json

import pulumi
import pulumi_aws as aws

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
    DatadogApiKey,
    DatadogApiKeyArgs,
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
    cluster_name: pulumi.Input[str]
    region: pulumi.Input[str]
    availability_zones: pulumi.Input[list[str]]
    organization_id: pulumi.Input[str]
    pinecone_api_key: pulumi.Input[str]

    # networking
    vpc_cidr: pulumi.Input[str] = "10.0.0.0/16"

    # kubernetes
    kubernetes_version: pulumi.Input[str] = "1.33"
    node_pools: Optional[list[NodePool]] = None

    # dns
    parent_dns_zone_name: pulumi.Input[str] = "byoc.pinecone.io"

    # features
    public_access_enabled: bool = True  # false = private access only via privatelink

    # pinecone api
    api_url: pulumi.Input[str] = "https://api.pinecone.io"
    global_env: pulumi.Input[str] = "prod"
    auth0_domain: pulumi.Input[str] = "https://login.pinecone.io"
    gcp_project: Optional[pulumi.Input[str]] = None  # defaults based on global_env

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

        self._environment = Environment(
            f"{name}-environment",
            EnvironmentArgs(
                cloud="aws",
                region=args.region,
                global_env=args.global_env,
                org_id=args.organization_id,
                api_url=args.api_url,
                secret=args.pinecone_api_key,
            ),
            opts=child_opts,
        )

        self._service_account = ServiceAccount(
            f"{name}-service-account",
            ServiceAccountArgs(
                name=f"{args.cluster_name}-sa",
                org_id=args.organization_id,
                api_url=args.api_url,
                secret=args.pinecone_api_key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._environment]),
        )

        self._api_key = ApiKey(
            f"{name}-api-key",
            ApiKeyArgs(
                org_id=args.organization_id,
                project_name=args.cluster_name,
                key_name=f"{args.cluster_name}-key",
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
            f"{name}-datadog-api-key",
            DatadogApiKeyArgs(
                organization_id=args.organization_id,
                environment_name=self._environment.env_name,
                api_url=args.api_url,
                secret=args.pinecone_api_key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._environment]),
        )

        self._vpc = VPC(f"{name}-vpc", config, opts=child_opts)

        self._eks = EKS(
            f"{name}-eks",
            config,
            self._vpc,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vpc]),
        )

        self._s3 = S3Buckets(
            f"{name}-s3",
            config,
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
            f"{name}-storage-integration-role",
            name=f"{config.resource_prefix}-storage-integration",
            assume_role_policy=assume_role_policy,
            tags=config.tags(Name=f"{config.resource_prefix}-storage-integration"),
            opts=child_opts,
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-storage-integration-policy",
            role=self._storage_integration_role.id,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            opts=child_opts,
        )
        # allow ec2 node role to assume storage integration role (for data-importer)
        aws.iam.RolePolicy(
            f"{name}-ec2-allow-assume-role",
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
            f"{name}-dns",
            subdomain=self._subdomain.apply(lambda name: name.removesuffix(".byoc")),
            parent_zone_name=args.parent_dns_zone_name,
            organization_id=args.organization_id,
            environment_name=self._environment.env_name,
            api_url=args.api_url,
            cpgw_secret=args.pinecone_api_key,
            tags={"pinecone:cell": args.cluster_name, "pinecone:managed-by": "pulumi"},
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._environment]),
        )

        self._rds = RDS(
            f"{name}-rds",
            config,
            self._vpc,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._vpc]),
        )

        self._k8s_addons = K8sAddons(
            f"{name}-k8s-addons",
            config,
            self._eks,
            self._vpc.vpc_id,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        # NLB for private endpoint access (creates private ALB + NLB)
        # must be after K8sAddons so ALB Controller is available
        self._nlb = NLB(
            f"{name}-nlb",
            config,
            self._vpc,
            self._dns,
            k8s_provider=self._eks.provider,
            cluster_security_group_id=self._eks.cluster_security_group_id,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._vpc, self._dns, self._eks, self._k8s_addons],
            ),
        )

        self._k8s_secrets = K8sSecrets(
            f"{name}-k8s-secrets",
            k8s_provider=self._eks.provider,
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
            f"{name}-pulumi-operator",
            config,
            oidc_provider_arn=self._eks.oidc_provider_arn,
            oidc_provider_url=self._eks.oidc_provider_url,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        # allow ec2 nodes to use pulumi kms key (for stack crds using node credentials)
        aws.iam.RolePolicy(
            f"{name}-ec2-allow-kms",
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
            "cell_name": args.cluster_name,
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
        }

        self._k8s_configmaps = K8sConfigMaps(
            f"{name}-k8s-configmaps",
            k8s_provider=self._eks.provider,
            cloud="aws",
            cell_name=args.cluster_name,
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
        self._ecr_refresher = EcrCredentialRefresher(
            f"{name}-ecr-refresher",
            k8s_provider=self._eks.provider,
            cpgw_url=args.api_url,
            organization_id=args.organization_id,
            environment_name=self._environment.env_name,
            pinecone_api_key=args.pinecone_api_key,  # TODO: temp auth, see ecr_refresher.py
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._k8s_secrets]),
        )

        # pinetools for cluster management
        self._pinetools = Pinetools(
            f"{name}-pinetools",
            k8s_provider=self._eks.provider,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self._eks]),
        )

        self.register_outputs(
            {
                "cluster_name": args.cluster_name,
                "region": args.region,
                "organization_id": args.organization_id,
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
                "datadog_api_key_id": self._datadog_api_key.key_id,
                "customer_tags": args.tags or {},
                "pulumi_backend_url": self._pulumi_operator.backend_url,
                "pulumi_secrets_provider": self._pulumi_operator.secrets_provider,
                "storage_integration_role_arn": self._storage_integration_role.arn,
            }
        )

    def _build_config(self, args: PineconeAWSClusterArgs):
        from config import Config, NodePoolConfig, DatabaseConfig

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
                        taints=[],
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
            cell_name=args.cluster_name,
            region=args.region,
            environment="prod",
            subdomain=args.cluster_name,
            organization_id=args.organization_id,
            api_url=args.api_url,
            availability_zones=args.availability_zones,
            vpc_cidr=args.vpc_cidr,
            kubernetes_version=args.kubernetes_version,
            node_pools=node_pools,
            parent_zone_id=None,
            parent_zone_name=args.parent_dns_zone_name,
            database=DatabaseConfig(),
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
