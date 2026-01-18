"""
EKS component for Pinecone BYOC infrastructure.

Creates a managed EKS cluster with configurable node groups.
"""

from typing import Optional
import json

import pulumi
import pulumi_aws as aws
import pulumi_eks as eks
import pulumi_kubernetes as k8s

from config import Config
from .vpc import VPC


class EKS(pulumi.ComponentResource):
    """
    Creates an EKS cluster with:
    - Managed node groups based on configuration
    - IAM roles for cluster and nodes
    - Security groups for cluster communication
    - OIDC provider for IAM Roles for Service Accounts (IRSA)
    """

    def __init__(
        self,
        name: str,
        config: Config,
        vpc: VPC,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:EKS", name, None, opts)

        self.config = config
        child_opts = pulumi.ResourceOptions(parent=self)

        # Create IAM role for EKS cluster
        cluster_role = self._create_cluster_role(name, child_opts)

        # Create IAM role for node groups
        self._node_role = self._create_node_role(name, child_opts)

        # Create EKS cluster using pulumi-eks
        # Skip default node group - we create managed node groups with AL2023 AMI
        # TODO: use naming convention `cluster-{cell_name}` to match nodepool.yaml template
        # which expects clusterName: cluster-{{ $.Values.cell_name }}
        self.cluster = eks.Cluster(
            f"{name}-cluster",
            name=config.resource_prefix,
            vpc_id=vpc.vpc_id,
            public_subnet_ids=vpc.public_subnet_ids,
            private_subnet_ids=vpc.private_subnet_ids,
            version=config.kubernetes_version,
            skip_default_node_group=True,
            instance_roles=[self._node_role],
            create_oidc_provider=True,
            endpoint_private_access=True,
            endpoint_public_access=True,
            enabled_cluster_log_types=[
                "api",
                "audit",
                "authenticator",
                "controllerManager",
                "scheduler",
            ],
            tags=config.tags(),
            opts=child_opts,
        )

        # Create managed node groups
        self.node_groups: list[aws.eks.NodeGroup] = []
        for np_config in config.node_pools:
            node_group = self._create_node_group(
                name, np_config, vpc, self._node_role, child_opts
            )
            self.node_groups.append(node_group)

        # Create K8s provider from kubeconfig
        self._k8s_provider = k8s.Provider(
            f"{name}-k8s-provider",
            kubeconfig=self.cluster.kubeconfig,
            opts=child_opts,
        )

        # Register outputs
        self.register_outputs(
            {
                "cluster_name": self.cluster.eks_cluster.name,
                "kubeconfig": self.cluster.kubeconfig,
                "oidc_provider_arn": self.cluster.core.oidc_provider.arn,
            }
        )

    def _create_cluster_role(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> aws.iam.Role:
        """Create IAM role for EKS cluster."""
        assume_role_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "eks.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )

        role = aws.iam.Role(
            f"{name}-cluster-role",
            assume_role_policy=assume_role_policy,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-cluster-role"),
            opts=opts,
        )

        # Attach required policies
        for policy_arn in [
            "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
            "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController",
        ]:
            policy_name = policy_arn.split("/")[-1]
            aws.iam.RolePolicyAttachment(
                f"{name}-cluster-{policy_name}",
                role=role.name,
                policy_arn=policy_arn,
                opts=opts,
            )

        return role

    def _create_node_role(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> aws.iam.Role:
        """Create IAM role for EKS node groups."""
        assume_role_policy = json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        )

        role = aws.iam.Role(
            f"{name}-node-role",
            assume_role_policy=assume_role_policy,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-node-role"),
            opts=opts,
        )

        # Attach required policies for nodes
        # S3FullAccess enables data plane services (shard-manager, etc.) to access S3
        for policy_arn in [
            "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
            "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
            "arn:aws:iam::aws:policy/AmazonRoute53FullAccess",
        ]:
            policy_name = policy_arn.split("/")[-1]
            aws.iam.RolePolicyAttachment(
                f"{name}-node-{policy_name}",
                role=role.name,
                policy_arn=policy_arn,
                opts=opts,
            )

        return role

    def _create_launch_template(
        self,
        name: str,
        np_config,
        opts: pulumi.ResourceOptions,
    ) -> aws.ec2.LaunchTemplate:
        """Create a launch template for the node group with IMDS settings."""
        resource_name = f"{self.config.resource_prefix}-{np_config.name}-lt"

        return aws.ec2.LaunchTemplate(
            f"{name}-lt-{np_config.name}",
            name=resource_name,
            block_device_mappings=[
                aws.ec2.LaunchTemplateBlockDeviceMappingArgs(
                    device_name="/dev/xvda",
                    ebs=aws.ec2.LaunchTemplateBlockDeviceMappingEbsArgs(
                        volume_type="gp3",
                        volume_size=np_config.disk_size_gb,
                        delete_on_termination="true",
                    ),
                ),
            ],
            update_default_version=True,
            # IMDS hop limit of 2 required for pods to access instance metadata
            # http_tokens="optional" allows both IMDSv1 and IMDSv2
            metadata_options=aws.ec2.LaunchTemplateMetadataOptionsArgs(
                http_put_response_hop_limit=2,
                http_tokens="optional",
            ),
            tags=self.config.tags(Name=resource_name),
            opts=opts,
        )

    def _create_node_group(
        self,
        name: str,
        np_config,
        vpc: VPC,
        node_role: aws.iam.Role,
        opts: pulumi.ResourceOptions,
    ) -> aws.eks.NodeGroup:
        """Create a managed node group."""
        # Create launch template with IMDS settings
        launch_template = self._create_launch_template(name, np_config, opts)

        # Build labels
        labels = {
            "pinecone.io/cell": self.config.cell_name,
            "pinecone.io/nodepool": np_config.name,
            **np_config.labels,
        }

        # Build taints
        taints = [
            aws.eks.NodeGroupTaintArgs(
                key=t.key,
                value=t.value,
                effect=t.effect.upper().replace("SCHEDULE", "_SCHEDULE"),
            )
            for t in np_config.taints
        ]

        return aws.eks.NodeGroup(
            f"{name}-ng-{np_config.name}",
            cluster_name=self.cluster.eks_cluster.name,
            node_group_name=f"{self.config.resource_prefix}-{np_config.name}",
            node_role_arn=node_role.arn,
            subnet_ids=vpc.private_subnet_ids,
            ami_type="AL2023_x86_64_STANDARD",
            instance_types=[np_config.instance_type],
            # disk_size is configured in launch template
            scaling_config=aws.eks.NodeGroupScalingConfigArgs(
                desired_size=np_config.desired_size,
                min_size=np_config.min_size,
                max_size=np_config.max_size,
            ),
            launch_template=aws.eks.NodeGroupLaunchTemplateArgs(
                id=launch_template.id,
                version=launch_template.latest_version.apply(str),
            ),
            labels=labels,
            taints=taints if taints else None,
            tags=self.config.tags(
                Name=f"{self.config.resource_prefix}-{np_config.name}"
            ),
            opts=opts,
        )

    @property
    def kubeconfig(self) -> pulumi.Output:
        return self.cluster.kubeconfig

    @property
    def provider(self) -> pulumi.ProviderResource:
        return self._k8s_provider

    @property
    def cluster_name(self) -> pulumi.Output[str]:
        return self.cluster.eks_cluster.name

    @property
    def oidc_provider_arn(self) -> pulumi.Output[str]:
        return self.cluster.core.oidc_provider.arn

    @property
    def oidc_provider_url(self) -> pulumi.Output[str]:
        return self.cluster.core.oidc_provider.url

    @property
    def node_role_arn(self) -> pulumi.Output[str]:
        return self._node_role.arn

    @property
    def node_role_name(self) -> pulumi.Output[str]:
        return self._node_role.name

    @property
    def cluster_security_group_id(self) -> pulumi.Output[str]:
        # get the auto-created cluster security group by EKS tag
        return self.cluster.kubeconfig.apply(
            lambda _: aws.ec2.get_security_group_output(
                tags={"aws:eks:cluster-name": self.config.resource_prefix},
            )
        ).apply(lambda sg: sg.id)
