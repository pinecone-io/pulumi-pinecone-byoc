"""VNet component for Azure infrastructure."""

import ipaddress

import pulumi
from pulumi_azure_native import network, resources

from config.azure import AzureConfig


class VNet(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        cell_name: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:VNet", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        self.resource_group = resources.ResourceGroup(
            f"{name}-rg",
            resource_group_name=self._cell_name.apply(
                lambda cn: f"{cn}-{config.region}-rg"
            ),
            location=config.region,
            tags=config.tags(),
            opts=child_opts,
        )

        self._rg_name = self.resource_group.name

        # single NAT gateway with public IP for outbound traffic
        self.public_ip = network.PublicIPAddress(
            f"{name}-nat-ip",
            public_ip_address_name=self._cell_name.apply(lambda cn: f"nat-ip-{cn}"),
            resource_group_name=self._rg_name,
            location=config.region,
            sku=network.PublicIPAddressSkuArgs(
                name=network.PublicIPAddressSkuName.STANDARD,
            ),
            public_ip_allocation_method=network.IPAllocationMethod.STATIC,
            tags=config.tags(),
            opts=child_opts,
        )

        self.nat_gateway = network.NatGateway(
            f"{name}-nat",
            nat_gateway_name=self._cell_name.apply(lambda cn: f"nat-{cn}"),
            resource_group_name=self._rg_name,
            location=config.region,
            sku=network.NatGatewaySkuArgs(
                name=network.NatGatewaySkuName.STANDARD,
            ),
            public_ip_addresses=[
                network.SubResourceArgs(id=self.public_ip.id),
            ],
            tags=config.tags(),
            opts=child_opts,
        )

        # derive db subnet from vpc_cidr: next adjacent /16 block
        aks_net = ipaddress.IPv4Network(config.vpc_cidr)
        db_net = ipaddress.IPv4Network(
            f"{aks_net.network_address + aks_net.num_addresses}/{aks_net.prefixlen}"
        )
        # PLS subnet: /27 block after the DB subnet for Private Link Service NAT IPs
        pls_net = ipaddress.IPv4Network(f"{db_net.network_address + db_net.num_addresses}/27")
        # supernet covering AKS, DB, and PLS subnets
        vnet_supernet = ipaddress.collapse_addresses([aks_net, db_net, pls_net])

        self.vnet = network.VirtualNetwork(
            f"{name}-vnet",
            virtual_network_name=self._cell_name.apply(lambda cn: f"vnet-{cn}"),
            resource_group_name=self._rg_name,
            location=config.region,
            address_space=network.AddressSpaceArgs(
                address_prefixes=[str(n) for n in vnet_supernet],
            ),
            tags=config.tags(),
            opts=child_opts,
        )

        # aks subnet with Microsoft.Storage service endpoint
        self.aks_subnet = network.Subnet(
            f"{name}-aks-subnet",
            subnet_name=self._cell_name.apply(lambda cn: f"aks-subnet-{cn}"),
            resource_group_name=self._rg_name,
            virtual_network_name=self.vnet.name,
            address_prefix=config.vpc_cidr,
            service_endpoints=[
                network.ServiceEndpointPropertiesFormatArgs(
                    service="Microsoft.Storage",
                ),
            ],
            nat_gateway=network.SubResourceArgs(id=self.nat_gateway.id),
            opts=child_opts,
        )

        # flexible server subnet with PostgreSQL delegation
        self.db_subnet = network.Subnet(
            f"{name}-db-subnet",
            subnet_name=self._cell_name.apply(lambda cn: f"db-subnet-{cn}"),
            resource_group_name=self._rg_name,
            virtual_network_name=self.vnet.name,
            address_prefix=str(db_net),
            delegations=[
                network.DelegationArgs(
                    name="postgresql-delegation",
                    service_name="Microsoft.DBforPostgreSQL/flexibleServers",
                ),
            ],
            opts=child_opts,
        )

        # PLS subnet for Private Link Service NAT IPs
        self.pls_subnet = network.Subnet(
            f"{name}-pls-subnet",
            subnet_name=self._cell_name.apply(lambda cn: f"pls-subnet-{cn}"),
            resource_group_name=self._rg_name,
            virtual_network_name=self.vnet.name,
            address_prefix=str(pls_net),
            private_link_service_network_policies=network.VirtualNetworkPrivateLinkServiceNetworkPolicies.DISABLED,
            opts=child_opts,
        )

        self.register_outputs(
            {
                "vnet_id": self.vnet.id,
                "vnet_name": self.vnet.name,
                "aks_subnet_id": self.aks_subnet.id,
                "db_subnet_id": self.db_subnet.id,
                "pls_subnet_id": self.pls_subnet.id,
                "resource_group_name": self._rg_name,
            }
        )

    @property
    def vnet_id(self) -> pulumi.Output[str]:
        return self.vnet.id

    @property
    def vnet_name(self) -> pulumi.Output[str]:
        return self.vnet.name

    @property
    def aks_subnet_id(self) -> pulumi.Output[str]:
        return self.aks_subnet.id

    @property
    def db_subnet_id(self) -> pulumi.Output[str]:
        return self.db_subnet.id

    @property
    def pls_subnet_id(self) -> pulumi.Output[str]:
        return self.pls_subnet.id

    @property
    def pls_subnet_name(self) -> pulumi.Output[str]:
        return self.pls_subnet.name.apply(lambda n: n or "")

    @property
    def resource_group_id(self) -> pulumi.Output[str]:
        return self.resource_group.id

    @property
    def resource_group_name(self) -> pulumi.Output[str]:
        return self._rg_name
