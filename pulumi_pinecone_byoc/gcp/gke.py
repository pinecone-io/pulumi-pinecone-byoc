"""GKE cluster infrastructure with Workload Identity."""

from typing import Optional

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s
from config.gcp import GCPConfig


class ServiceAccounts:
    """GCP service accounts for Workload Identity."""

    def __init__(
        self,
        nodepool_sa: gcp.serviceaccount.Account,
        reader_sa: gcp.serviceaccount.Account,
        writer_sa: gcp.serviceaccount.Account,
        dns_sa: gcp.serviceaccount.Account,
        pulumi_sa: gcp.serviceaccount.Account,
        prometheus_sa: gcp.serviceaccount.Account,
    ):
        self.nodepool_sa = nodepool_sa
        self.reader_sa = reader_sa
        self.writer_sa = writer_sa
        self.dns_sa = dns_sa
        self.pulumi_sa = pulumi_sa
        self.prometheus_sa = prometheus_sa


class GKEResult:
    """Result object containing GKE cluster resources."""

    def __init__(
        self,
        cluster: gcp.container.Cluster,
        node_pools: list[gcp.container.NodePool],
        service_accounts: ServiceAccounts,
        kubeconfig: pulumi.Output[str],
        k8s_provider: k8s.Provider,
    ):
        self.cluster = cluster
        self.node_pools = node_pools
        self.service_accounts = service_accounts
        self.kubeconfig = kubeconfig
        self.k8s_provider = k8s_provider


class GKE(pulumi.ComponentResource):
    """GKE cluster with Workload Identity and node pools."""

    def __init__(
        self,
        name: str,
        config: GCPConfig,
        network_id: pulumi.Output[str],
        subnet_id: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:GKE", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)
        self._resource_suffix = self._cell_name.apply(lambda cn: cn[-4:])

        cluster = gcp.container.Cluster(
            f"{name}-cluster",
            name=self._cell_name.apply(lambda cn: f"cluster-{cn}"),
            location=config.region,
            node_locations=config.availability_zones,
            network=network_id,
            subnetwork=subnet_id,
            networking_mode="VPC_NATIVE",
            datapath_provider="ADVANCED_DATAPATH",
            initial_node_count=1,
            remove_default_node_pool=True,
            ip_allocation_policy=gcp.container.ClusterIpAllocationPolicyArgs(
                cluster_ipv4_cidr_block="/14",
                services_ipv4_cidr_block="/18",
            ),
            private_cluster_config=gcp.container.ClusterPrivateClusterConfigArgs(
                enable_private_nodes=True,
                enable_private_endpoint=False,
                master_ipv4_cidr_block="10.100.0.0/28",
            ),
            master_authorized_networks_config=gcp.container.ClusterMasterAuthorizedNetworksConfigArgs(
                cidr_blocks=[
                    gcp.container.ClusterMasterAuthorizedNetworksConfigCidrBlockArgs(
                        cidr_block="0.0.0.0/0",
                        display_name="All networks",
                    )
                ]
            ),
            workload_identity_config=gcp.container.ClusterWorkloadIdentityConfigArgs(
                workload_pool=f"{config.project}.svc.id.goog"
            ),
            addons_config=gcp.container.ClusterAddonsConfigArgs(
                dns_cache_config=gcp.container.ClusterAddonsConfigDnsCacheConfigArgs(
                    enabled=True
                ),
            ),
            binary_authorization=gcp.container.ClusterBinaryAuthorizationArgs(
                evaluation_mode="PROJECT_SINGLETON_POLICY_ENFORCE"
            ),
            release_channel=gcp.container.ClusterReleaseChannelArgs(
                channel="UNSPECIFIED"
            ),
            cluster_autoscaling=gcp.container.ClusterClusterAutoscalingArgs(
                enabled=False,
                autoscaling_profile="OPTIMIZE_UTILIZATION",
            ),
            monitoring_config=gcp.container.ClusterMonitoringConfigArgs(
                managed_prometheus=gcp.container.ClusterMonitoringConfigManagedPrometheusArgs(
                    enabled=False
                )
            ),
            deletion_protection=config.database.deletion_protection,
            resource_labels=config.tags(),
            opts=pulumi.ResourceOptions(parent=self),
        )

        nodepool_sa = gcp.serviceaccount.Account(
            f"{name}-np-sa",
            account_id=self._cell_name.apply(lambda cn: f"np-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"Nodepool service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        reader_sa = gcp.serviceaccount.Account(
            f"{name}-read-sa",
            account_id=self._cell_name.apply(lambda cn: f"read-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"Reader service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        writer_sa = gcp.serviceaccount.Account(
            f"{name}-write-sa",
            account_id=self._cell_name.apply(lambda cn: f"write-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"Writer service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        dns_sa = gcp.serviceaccount.Account(
            f"{name}-dns-sa",
            account_id=self._cell_name.apply(lambda cn: f"dns-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"DNS service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        pulumi_sa = gcp.serviceaccount.Account(
            f"{name}-pulumi-sa",
            account_id=self._cell_name.apply(lambda cn: f"pulumi-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"Pulumi service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        prometheus_sa = gcp.serviceaccount.Account(
            f"{name}-prometheus-sa",
            account_id=self._cell_name.apply(lambda cn: f"prom-sa-{cn}"),
            display_name=self._cell_name.apply(
                lambda cn: f"Prometheus service account for {cn}"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-np-sa-iam",
            project=config.project,
            member=nodepool_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/iam.serviceAccountAdmin",
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-np-sa-storage",
            project=config.project,
            member=nodepool_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/storage.admin",
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-read-sa-storage",
            project=config.project,
            member=reader_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/storage.objectViewer",
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-write-sa-storage",
            project=config.project,
            member=writer_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/storage.admin",
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-write-sa-iam",
            project=config.project,
            member=writer_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/iam.serviceAccountAdmin",
            opts=pulumi.ResourceOptions(parent=self),
        )

        gcp.projects.IAMMember(
            f"{name}-dns-sa",
            project=config.project,
            member=dns_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/dns.admin",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # allows cert-manager K8s SA to impersonate DNS GCP SA for DNS-01 ACME challenges
        gcp.serviceaccount.IAMBinding(
            f"{name}-dns-sa-workload-identity",
            service_account_id=dns_sa.name,
            role="roles/iam.workloadIdentityUser",
            members=[
                pulumi.Output.all(config.project, self._cell_name).apply(
                    lambda args: f"serviceAccount:{args[0]}.svc.id.goog[gloo-system/certmanager-certgen]"
                )
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[dns_sa]),
        )

        # allows pulumi-operator K8s SA to impersonate Pulumi GCP SA for GCS state access
        gcp.serviceaccount.IAMBinding(
            f"{name}-pulumi-sa-workload-identity",
            service_account_id=pulumi_sa.name,
            role="roles/iam.workloadIdentityUser",
            members=[
                pulumi.Output.all(config.project, self._cell_name).apply(
                    lambda args: f"serviceAccount:{args[0]}.svc.id.goog[pulumi-kubernetes-operator/pulumi-k8s-operator]"
                )
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[pulumi_sa]),
        )

        gcp.projects.IAMMember(
            f"{name}-pulumi-sa-k8s",
            project=config.project,
            member=pulumi_sa.email.apply(lambda email: f"serviceAccount:{email}"),
            role="roles/container.serviceAgent",
            opts=pulumi.ResourceOptions(parent=self),
        )

        # allows multiple K8s service accounts to impersonate write GCP SA for GCS/AlloyDB access
        gcp.serviceaccount.IAMBinding(
            f"{name}-writer-sa-workload-identity",
            service_account_id=writer_sa.name,
            role="roles/iam.workloadIdentityUser",
            members=pulumi.Output.all(config.project).apply(
                lambda args: [
                    f"serviceAccount:{args[0]}.svc.id.goog[{sa}]"
                    for sa in config.writer_k8s_service_accounts
                ]
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[writer_sa]),
        )

        # allows K8s service accounts to impersonate read GCP SA for GCS read-only access
        gcp.serviceaccount.IAMBinding(
            f"{name}-reader-sa-workload-identity",
            service_account_id=reader_sa.name,
            role="roles/iam.workloadIdentityUser",
            members=pulumi.Output.all(config.project).apply(
                lambda args: [
                    f"serviceAccount:{args[0]}.svc.id.goog[{sa}]"
                    for sa in config.reader_k8s_service_accounts
                ]
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[reader_sa]),
        )

        # allows prometheus K8s SA to impersonate prometheus GCP SA for AWS STS token exchange
        gcp.serviceaccount.IAMBinding(
            f"{name}-prometheus-sa-workload-identity",
            service_account_id=prometheus_sa.name,
            role="roles/iam.workloadIdentityUser",
            members=[
                pulumi.Output.all(config.project).apply(
                    lambda args: f"serviceAccount:{args[0]}.svc.id.goog[prometheus/prometheus-server]"
                )
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[prometheus_sa]),
        )

        node_pools = []
        for np_config in config.node_pools:
            node_pool = self._create_node_pool(
                name=name,
                config=config,
                np_config=np_config,
                cluster_id=cluster.id,
                nodepool_sa_email=nodepool_sa.email,
            )
            node_pools.append(node_pool)

        kubeconfig = pulumi.Output.all(
            cluster.master_auth.cluster_ca_certificate,
            cluster.endpoint,
            cluster.name,
        ).apply(
            lambda args: f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[0]}
    server: https://{args[1]}
  name: {args[2]}
contexts:
- context:
    cluster: {args[2]}
    user: {args[2]}
  name: {args[2]}
current-context: {args[2]}
kind: Config
preferences: {{}}
users:
- name: {args[2]}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      installHint: Install gke-gcloud-auth-plugin for use with kubectl
      provideClusterInfo: true
"""
        )

        k8s_provider = k8s.Provider(
            f"{name}-k8s-provider",
            kubeconfig=kubeconfig,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[cluster]),
        )

        service_accounts = ServiceAccounts(
            nodepool_sa=nodepool_sa,
            reader_sa=reader_sa,
            writer_sa=writer_sa,
            dns_sa=dns_sa,
            pulumi_sa=pulumi_sa,
            prometheus_sa=prometheus_sa,
        )

        self._result = GKEResult(
            cluster=cluster,
            node_pools=node_pools,
            service_accounts=service_accounts,
            kubeconfig=kubeconfig,
            k8s_provider=k8s_provider,
        )

        self.register_outputs(
            {
                "cluster_name": cluster.name,
                "cluster_endpoint": cluster.endpoint,
            }
        )

    def _create_node_pool(
        self,
        name: str,
        config: GCPConfig,
        np_config,
        cluster_id: pulumi.Output[str],
        nodepool_sa_email: pulumi.Output[str],
    ) -> gcp.container.NodePool:
        base_labels = dict(np_config.labels) if np_config.labels else {}
        base_labels["nodepool_name"] = np_config.name
        base_labels.update(config.tags())

        # labels must be strings, so we need to use apply
        labels = self._cell_name.apply(
            lambda cn: {"pinecone.io/cell": cn, **base_labels}
        )

        taints = [
            gcp.container.NodePoolNodeConfigTaintArgs(
                key=taint.key,
                value=str(taint.value),
                effect=taint.effect,
            )
            for taint in (np_config.taints or [])
        ]

        nvme_storage_config = None
        ephemeral_storage_config = None
        if np_config.ssd_count:
            if np_config.is_nvme:
                nvme_storage_config = (
                    gcp.container.NodePoolNodeConfigLocalNvmeSsdBlockConfigArgs(
                        local_ssd_count=np_config.ssd_count
                    )
                )
            else:
                ephemeral_storage_config = (
                    gcp.container.NodePoolNodeConfigEphemeralStorageConfigArgs(
                        local_ssd_count=np_config.ssd_count
                    )
                )
        else:
            nvme_storage_config = (
                gcp.container.NodePoolNodeConfigLocalNvmeSsdBlockConfigArgs(
                    local_ssd_count=0
                )
            )

        autoscaling = gcp.container.NodePoolAutoscalingArgs(
            min_node_count=np_config.min_size,
            max_node_count=np_config.max_size,
            location_policy="BALANCED",
        )

        node_pool_name = f"{name}-np-{np_config.name}"[:32]

        node_pool = gcp.container.NodePool(
            node_pool_name,
            cluster=cluster_id,
            autoscaling=autoscaling,
            node_config=gcp.container.NodePoolNodeConfigArgs(
                machine_type=np_config.machine_type,
                min_cpu_platform="Intel Ice Lake",
                labels=labels,
                resource_labels=config.tags(),
                taints=taints or None,
                ephemeral_storage_config=ephemeral_storage_config,
                local_nvme_ssd_block_config=nvme_storage_config,
                oauth_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                service_account=nodepool_sa_email,
            ),
            node_locations=config.availability_zones,
            management=gcp.container.NodePoolManagementArgs(
                auto_repair=False,
                auto_upgrade=False,
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        return node_pool

    @property
    def cluster(self) -> gcp.container.Cluster:
        return self._result.cluster

    @property
    def node_pools(self) -> list[gcp.container.NodePool]:
        return self._result.node_pools

    @property
    def service_accounts(self) -> ServiceAccounts:
        return self._result.service_accounts

    @property
    def kubeconfig(self) -> pulumi.Output[str]:
        return self._result.kubeconfig

    @property
    def k8s_provider(self) -> k8s.Provider:
        return self._result.k8s_provider

    @property
    def result(self) -> GKEResult:
        return self._result
