"""Azure DNS infrastructure for domain management."""

import pulumi
from pulumi_azure_native import network

from ..common.naming import DNS_CNAMES
from ..common.providers import DnsDelegation, DnsDelegationArgs


class DNS(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        subdomain: pulumi.Input[str],
        parent_zone_name: pulumi.Input[str],
        api_url: pulumi.Input[str],
        cpgw_api_key: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        resource_group_name: pulumi.Input[str],
        location: str,
        tags: dict[str, str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:DNS", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        def build_fqdn(sub: str) -> str:
            return f"{sub}.{parent_zone_name}"

        fqdn = pulumi.Output.from_input(subdomain).apply(build_fqdn)

        # public IP for external ingress
        external_ip = network.PublicIPAddress(
            f"{name}-external-ip",
            public_ip_address_name=self._cell_name.apply(lambda cn: f"externalip-{cn}"),
            resource_group_name=resource_group_name,
            location=location,
            sku=network.PublicIPAddressSkuArgs(
                name=network.PublicIPAddressSkuName.STANDARD,
            ),
            public_ip_allocation_method=network.IPAllocationMethod.STATIC,
            tags=tags or {},
            opts=child_opts,
        )

        dns_zone = network.Zone(
            f"{name}-zone",
            zone_name=fqdn,
            resource_group_name=resource_group_name,
            location="global",
            tags=tags or {},
            opts=child_opts,
        )

        ingress_a_record = network.RecordSet(
            f"{name}-ingress-a-record",
            zone_name=dns_zone.name,
            relative_record_set_name="ingress",
            record_type="A",
            a_records=[
                network.ARecordArgs(
                    ipv4_address=external_ip.ip_address.apply(lambda ip: ip or ""),
                ),
            ],
            ttl=300,
            resource_group_name=resource_group_name,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[dns_zone]),
        )

        cname_records = []
        for cname in DNS_CNAMES:
            cname_record = network.RecordSet(
                f"{name}-{cname.replace('*.', 'wildcard-').replace('.', '-')}-cname",
                zone_name=dns_zone.name,
                relative_record_set_name=cname,
                record_type="CNAME",
                cname_record=fqdn.apply(
                    lambda s, c=cname: network.CnameRecordArgs(cname=f"ingress.{s}"),
                ),
                ttl=300,
                resource_group_name=resource_group_name,
                opts=pulumi.ResourceOptions(parent=self, depends_on=[dns_zone]),
            )
            cname_records.append(cname_record)

        dns_delegation = DnsDelegation(
            f"{name}-delegation",
            DnsDelegationArgs(
                subdomain=subdomain,
                nameservers=dns_zone.name_servers,
                api_url=api_url,
                cpgw_api_key=cpgw_api_key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[dns_zone]),
        )

        self._dns_zone = dns_zone
        self._external_ip = external_ip
        self._subdomain = fqdn
        self._ingress_a_record = ingress_a_record
        self._cname_records = cname_records
        self._dns_delegation = dns_delegation

        self.register_outputs(
            {
                "dns_zone_name": dns_zone.name,
                "external_ip": external_ip.ip_address,
                "subdomain": subdomain,
            }
        )

    @property
    def zone(self) -> network.Zone:
        return self._dns_zone

    @property
    def name_servers(self) -> pulumi.Output:
        return self._dns_zone.name_servers

    @property
    def external_ip(self) -> network.PublicIPAddress:
        return self._external_ip

    @property
    def subdomain(self) -> pulumi.Output[str]:
        return self._subdomain
