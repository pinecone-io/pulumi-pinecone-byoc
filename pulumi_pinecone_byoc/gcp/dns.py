"""Cloud DNS infrastructure for domain management."""

from typing import Optional

import pulumi
import pulumi_gcp as gcp
from ..common.providers import DnsDelegation, DnsDelegationArgs


class DNS(pulumi.ComponentResource):

    def __init__(
        self,
        name: str,
        subdomain: pulumi.Input[str],
        parent_zone_name: str,
        api_url: str,
        cpgw_api_key: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:DNS", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        def build_fqdn(sub: str) -> str:
            return f"{sub}.{parent_zone_name}"

        fqdn = pulumi.Output.from_input(subdomain).apply(build_fqdn)

        external_ip = gcp.compute.GlobalAddress(
            f"{name}-external-ip",
            name=self._cell_name.apply(lambda cn: f"externalip-{cn}"),
            opts=pulumi.ResourceOptions(parent=self),
        )

        dns_zone = gcp.dns.ManagedZone(
            f"{name}-zone",
            name=self._cell_name.apply(lambda cn: f"dns-zone-{cn}"),
            description=self._cell_name.apply(lambda cn: f"DNS zone for {cn}"),
            dns_name=fqdn.apply(lambda s: f"{s}."),
            opts=pulumi.ResourceOptions(parent=self),
        )

        ingress_a_record = gcp.dns.RecordSet(
            f"{name}-ingress-a-record",
            managed_zone=dns_zone.name,
            name=fqdn.apply(lambda s: f"ingress.{s}."),
            type="A",
            rrdatas=[external_ip.address],
            ttl=300,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[dns_zone]),
        )

        cnames = ["*.svc", "metrics", "prometheus"]
        cname_records = []
        for cname in cnames:
            cname_record = gcp.dns.RecordSet(
                f"{name}-{cname.replace('*.', 'wildcard-').replace('.', '-')}-cname",
                managed_zone=dns_zone.name,
                name=fqdn.apply(lambda s, c=cname: f"{c}.{s}."),
                type="CNAME",
                ttl=300,
                rrdatas=[fqdn.apply(lambda s: f"ingress.{s}.")],
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
                "external_ip": external_ip.address,
                "subdomain": subdomain,
            }
        )

    @property
    def dns_zone(self) -> gcp.dns.ManagedZone:
        return self._dns_zone

    @property
    def external_ip(self) -> gcp.compute.GlobalAddress:
        return self._external_ip

    @property
    def subdomain(self) -> pulumi.Output[str]:
        return self._subdomain

    @property
    def nameservers(self) -> pulumi.Output[list[str]]:
        return self._dns_zone.name_servers
