"""AKS cluster infrastructure with Workload Identity."""

import base64
import re
import socket
import time

import pulumi
import pulumi_kubernetes as k8s
from pulumi_azure_native import authorization, containerservice, managedidentity

from config.azure import AzureConfig
from config.base import NodePoolConfig

_AGENT_POOL_NAME_MAX_LEN = 12


def _pool_name(name: str) -> str:
    """Sanitize node pool name to max 12 alphanumeric chars."""
    return name.replace("-", "").replace("_", "")[:_AGENT_POOL_NAME_MAX_LEN]


class AKS(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        resource_group_name: pulumi.Input[str],
        subnet_id: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:AKS", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        # cluster managed identity
        cluster_identity = managedidentity.UserAssignedIdentity(
            f"{name}-cluster-identity",
            resource_group_name=resource_group_name,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # kubelet managed identity (node pools)
        kubelet_identity = managedidentity.UserAssignedIdentity(
            f"{name}-kubelet-identity",
            resource_group_name=resource_group_name,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # cluster identity needs Managed Identity Operator on kubelet identity
        # so AKS can assign the kubelet identity to node pools
        mi_operator_role = authorization.RoleAssignment(
            f"{name}-mi-operator-role",
            principal_id=cluster_identity.principal_id,
            principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=kubelet_identity.id.apply(
                lambda _: (
                    f"/subscriptions/{config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/f1a07417-d97a-45cb-824c-7a7467783830"
                )
            ),
            scope=kubelet_identity.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # cluster identity needs Network Contributor on the AKS subnet
        # so the cloud controller can create load balancers and manage PLS
        network_contributor_role = authorization.RoleAssignment(
            f"{name}-network-contributor-role",
            principal_id=cluster_identity.principal_id,
            principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=pulumi.Output.from_input(subnet_id).apply(
                lambda _: (
                    f"/subscriptions/{config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"
                )
            ),
            scope=subnet_id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # cluster identity needs Network Contributor on the resource group
        # so the cloud controller can manage public IPs for LoadBalancer Services
        authorization.RoleAssignment(
            f"{name}-rg-network-contributor-role",
            principal_id=cluster_identity.principal_id,
            principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=pulumi.Output.from_input(resource_group_name).apply(
                lambda _: (
                    f"/subscriptions/{config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4d97b98b-1d4f-4787-a291-c67834d212e7"
                )
            ),
            scope=pulumi.Output.from_input(resource_group_name).apply(
                lambda rg: f"/subscriptions/{config.subscription_id}/resourceGroups/{rg}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # default system pool from first node pool config
        default_np = config.node_pools[0] if config.node_pools else None
        default_pool_args = self._default_pool_args(config, default_np, subnet_id)

        managed_cluster = containerservice.ManagedCluster(
            f"{name}-cluster",
            resource_name_=self._cell_name.apply(lambda cn: f"cluster-{cn}"),
            resource_group_name=resource_group_name,
            kubernetes_version=config.kubernetes_version,
            dns_prefix=self._cell_name,
            enable_rbac=True,
            node_resource_group=pulumi.Output.from_input(resource_group_name).apply(
                lambda rg: f"{rg}-nodepool"
            ),
            identity=containerservice.ManagedClusterIdentityArgs(
                type=containerservice.ResourceIdentityType.USER_ASSIGNED,
                user_assigned_identities=[cluster_identity.id],
            ),
            identity_profile={
                "kubeletidentity": containerservice.UserAssignedIdentityArgs(
                    object_id=kubelet_identity.principal_id,
                    resource_id=kubelet_identity.id,
                ),
            },
            network_profile=containerservice.ContainerServiceNetworkProfileArgs(
                network_plugin="azure",
                dns_service_ip="112.0.0.10",
                service_cidr="112.0.0.0/16",
            ),
            auto_scaler_profile=containerservice.ManagedClusterPropertiesAutoScalerProfileArgs(
                balance_similar_node_groups="true",
                skip_nodes_with_local_storage="false",
            ),
            security_profile=containerservice.ManagedClusterSecurityProfileArgs(
                workload_identity=containerservice.ManagedClusterSecurityProfileWorkloadIdentityArgs(
                    enabled=True,
                ),
            ),
            oidc_issuer_profile=containerservice.ManagedClusterOIDCIssuerProfileArgs(
                enabled=True,
            ),
            sku=containerservice.ManagedClusterSKUArgs(
                name="Base",
                tier="Standard",
            ),
            agent_pool_profiles=[default_pool_args],
            tags=config.tags(),
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[mi_operator_role, network_contributor_role],
                ignore_changes=["agent_pool_profiles"],
            ),
        )

        # additional node pools (skip first, already used as default)
        self._agent_pools: list[containerservice.AgentPool] = []
        for np_config in config.node_pools[1:]:
            agent_pool = self._create_agent_pool(
                name=name,
                config=config,
                np_config=np_config,
                cluster_name=managed_cluster.name,
                resource_group_name=resource_group_name,
                subnet_id=subnet_id,
            )
            self._agent_pools.append(agent_pool)

        # kubeconfig from cluster credentials
        creds = containerservice.list_managed_cluster_user_credentials_output(
            resource_group_name=resource_group_name,
            resource_name=managed_cluster.name,
        )

        self._kubeconfig = creds.kubeconfigs[0].value.apply(
            lambda enc: AKS._wait_for_api_server(base64.b64decode(enc).decode())
        )

        self._k8s_provider = k8s.Provider(
            f"{name}-k8s-provider",
            kubeconfig=self._kubeconfig,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[managed_cluster]),
        )

        self._cluster = managed_cluster
        self._oidc_issuer_url = managed_cluster.oidc_issuer_profile.apply(
            lambda oidc: oidc.issuer_url if oidc else ""
        )
        self._kubelet_identity_object_id = kubelet_identity.principal_id
        self._cluster_identity = cluster_identity

        self.register_outputs(
            {
                "cluster_name": managed_cluster.name,
                "oidc_issuer_url": self._oidc_issuer_url,
            }
        )

    def _default_pool_args(
        self,
        config: AzureConfig,
        np_config: NodePoolConfig | None,
        subnet_id: pulumi.Input[str],
    ) -> containerservice.ManagedClusterAgentPoolProfileArgs:
        vm_size = np_config.vm_size if np_config else "Standard_D4s_v5"
        min_count = np_config.min_size if np_config else 1
        max_count = np_config.max_size if np_config else 10
        disk_size_gb = np_config.disk_size_gb if np_config else 100

        labels = {"nodepool_name": np_config.name} if np_config else {}
        taints = (
            [f"{t.key}={t.value}:{t.effect}" for t in np_config.taints]
            if np_config and np_config.taints
            else None
        )

        return containerservice.ManagedClusterAgentPoolProfileArgs(
            name=_pool_name(np_config.name if np_config else "default"),
            mode=containerservice.AgentPoolMode.SYSTEM,
            vm_size=vm_size,
            os_sku=containerservice.OSSKU.UBUNTU,
            type=containerservice.AgentPoolType.VIRTUAL_MACHINE_SCALE_SETS,
            enable_auto_scaling=True,
            min_count=min_count,
            count=min_count,
            max_count=max_count,
            os_disk_size_gb=disk_size_gb,
            vnet_subnet_id=subnet_id,
            availability_zones=config.availability_zones,
            node_labels=labels,
            node_taints=taints,
        )

    def _create_agent_pool(
        self,
        name: str,
        config: AzureConfig,
        np_config: NodePoolConfig,
        cluster_name: pulumi.Output[str],
        resource_group_name: pulumi.Input[str],
        subnet_id: pulumi.Input[str],
    ) -> containerservice.AgentPool:
        labels = {"nodepool_name": np_config.name}
        if np_config.labels:
            labels.update(np_config.labels)

        taints = (
            [f"{t.key}={t.value}:{t.effect}" for t in np_config.taints]
            if np_config.taints
            else None
        )

        return containerservice.AgentPool(
            f"{name}-np-{np_config.name}",
            agent_pool_name=_pool_name(np_config.name),
            resource_group_name=resource_group_name,
            resource_name_=cluster_name,
            vm_size=np_config.vm_size,
            os_sku=containerservice.OSSKU.UBUNTU,
            type=containerservice.AgentPoolType.VIRTUAL_MACHINE_SCALE_SETS,
            mode=containerservice.AgentPoolMode.USER,
            enable_auto_scaling=True,
            min_count=np_config.min_size,
            count=np_config.min_size,
            max_count=np_config.max_size,
            os_disk_size_gb=np_config.disk_size_gb,
            vnet_subnet_id=subnet_id,
            availability_zones=config.availability_zones,
            node_labels=labels,
            node_taints=taints,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self),
        )

    @staticmethod
    def _wait_for_api_server(kubeconfig: str) -> str:
        """Wait for AKS API server DNS to resolve before returning kubeconfig."""
        match = re.search(r"server:\s*https://([^:/\s]+)", kubeconfig)
        if match:
            hostname = match.group(1)
            for attempt in range(12):
                try:
                    socket.getaddrinfo(hostname, 443)
                    return kubeconfig
                except socket.gaierror:
                    pulumi.log.info(
                        f"Waiting for API server DNS ({hostname})... attempt {attempt + 1}/12"
                    )
                    time.sleep(5)
        return kubeconfig

    @property
    def cluster(self) -> containerservice.ManagedCluster:
        return self._cluster

    @property
    def k8s_provider(self) -> k8s.Provider:
        return self._k8s_provider

    @property
    def kubeconfig(self) -> pulumi.Output[str]:
        return self._kubeconfig

    @property
    def oidc_issuer_url(self) -> pulumi.Output[str]:
        return self._oidc_issuer_url

    @property
    def kubelet_identity_object_id(self) -> pulumi.Output[str]:
        return self._kubelet_identity_object_id

    @property
    def cluster_identity(self) -> managedidentity.UserAssignedIdentity:
        return self._cluster_identity
