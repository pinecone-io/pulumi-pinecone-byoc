"""VPC component for GCP infrastructure."""

from typing import Optional
import ipaddress

import pulumi
import pulumi_gcp as gcp

from config.gcp import GCPConfig


class VPC(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: GCPConfig,
        cell_name: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:VPC", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        # validate VPC CIDR doesn't overlap with hardcoded subnets
        vpc_net = ipaddress.ip_network(config.vpc_cidr)
        psc_net = ipaddress.ip_network("10.100.1.0/24")
        proxy_net = ipaddress.ip_network("10.100.2.0/24")

        if vpc_net.overlaps(psc_net) or vpc_net.overlaps(proxy_net):
            raise ValueError(
                f"VPC CIDR {config.vpc_cidr} overlaps with reserved ranges: "
                "10.100.1.0/24 (Private Service Connect), 10.100.2.0/24 (Regional Managed Proxy). "
                "Please choose a VPC CIDR that doesn't overlap with 10.100.0.0/16."
            )

        self.network = gcp.compute.Network(
            f"{name}-network",
            name=self._cell_name.apply(lambda cn: f"network-{cn}"),
            project=config.project,
            auto_create_subnetworks=False,
            opts=child_opts,
        )

        self.main_subnet = gcp.compute.Subnetwork(
            f"{name}-subnet",
            name=self._cell_name.apply(lambda cn: f"subnet-{cn}"),
            project=config.project,
            region=config.region,
            network=self.network.id,
            ip_cidr_range=config.vpc_cidr,
            private_ip_google_access=True,
            opts=child_opts,
        )

        self.psc_subnet = gcp.compute.Subnetwork(
            f"{name}-private-subnet",
            name=self._cell_name.apply(lambda cn: f"private-subnet-{cn}"),
            project=config.project,
            region=config.region,
            network=self.network.id,
            ip_cidr_range="10.100.1.0/24",
            purpose="PRIVATE_SERVICE_CONNECT",
            opts=child_opts,
        )

        self.proxy_subnet = gcp.compute.Subnetwork(
            f"{name}-private-proxy-network",
            name=self._cell_name.apply(lambda cn: f"private-proxy-network-{cn}"),
            project=config.project,
            region=config.region,
            network=self.network.id,
            ip_cidr_range="10.100.2.0/24",
            purpose="REGIONAL_MANAGED_PROXY",
            role="ACTIVE",
            opts=child_opts,
        )

        self.private_ip_range = gcp.compute.GlobalAddress(
            f"{name}-private-ip-range",
            name=self._cell_name.apply(lambda cn: f"private-ip-range-{cn}"),
            project=config.project,
            network=self.network.id,
            purpose="VPC_PEERING",
            address_type="INTERNAL",
            prefix_length=16,
            opts=child_opts,
        )

        self.private_connection = gcp.servicenetworking.Connection(
            f"{name}-private-connection",
            network=self.network.id,
            service="servicenetworking.googleapis.com",
            reserved_peering_ranges=[self.private_ip_range.name],
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self.private_ip_range]
            ),
        )

        self.router = gcp.compute.Router(
            f"{name}-router",
            name=self._cell_name.apply(lambda cn: f"router-{cn}"),
            project=config.project,
            region=config.region,
            network=self.network.id,
            opts=child_opts,
        )

        self.nat = gcp.compute.RouterNat(
            f"{name}-nat",
            name=self._cell_name.apply(lambda cn: f"nat-{cn}"),
            project=config.project,
            region=config.region,
            router=self.router.name,
            nat_ip_allocate_option="AUTO_ONLY",
            source_subnetwork_ip_ranges_to_nat="ALL_SUBNETWORKS_ALL_IP_RANGES",
            opts=child_opts,
        )

        self.register_outputs(
            {
                "network_id": self.network.id,
                "network_name": self.network.name,
                "main_subnet_id": self.main_subnet.id,
                "main_subnet_name": self.main_subnet.name,
                "psc_subnet_id": self.psc_subnet.id,
                "proxy_subnet_id": self.proxy_subnet.id,
            }
        )

    @property
    def network_id(self) -> pulumi.Output[str]:
        return self.network.id

    @property
    def network_name(self) -> pulumi.Output[str]:
        return self.network.name

    @property
    def main_subnet_id(self) -> pulumi.Output[str]:
        return self.main_subnet.id

    @property
    def main_subnet_name(self) -> pulumi.Output[str]:
        return self.main_subnet.name

    @property
    def private_ip_range_name(self) -> pulumi.Output[str]:
        return self.private_ip_range.name

    @property
    def psc_subnet_id(self) -> pulumi.Output[str]:
        return self.psc_subnet.id
