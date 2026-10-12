from typing import NamedTuple

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver


class Cut(NamedTuple):
    owner: str
    nameservers: set[str]


class Referral:
    def __init__(self, response: dns.message.Message, cell: str, zone: str):
        self.cell = dns.name.from_text(cell)
        self.zone = dns.name.from_text(zone)
        self.delegations = {
            rrset.name: {self.text(record.target) for record in rrset}
            for rrset in (*response.answer, *response.authority)
            if rrset.rdtype == dns.rdatatype.NS
        }

    @staticmethod
    def text(name: dns.name.Name) -> str:
        return name.to_text().rstrip(".").lower()

    @property
    def at_cell(self) -> set[str] | None:
        return self.delegations.get(self.cell)

    @property
    def cut_above_cell(self) -> Cut | None:
        between = [
            owner
            for owner in self.delegations
            if self.cell.is_subdomain(owner)
            and owner != self.cell
            and owner.is_subdomain(self.zone)
            and owner != self.zone
        ]
        if not between:
            return None
        owner = max(between, key=lambda name: len(name.labels))
        return Cut(self.text(owner), self.delegations[owner])


class DelegationCheck:
    def __init__(self, fqdn: str, resolver: dns.resolver.Resolver):
        self.fqdn = fqdn.rstrip(".").lower()
        self.resolver = resolver
        self.query = dns.message.make_query(self.fqdn, dns.rdatatype.NS)
        self.query.flags &= ~dns.flags.RD

    @staticmethod
    def public_resolver() -> dns.resolver.Resolver:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
        resolver.port = 53
        resolver.lifetime = 5
        try:
            resolver.resolve(".", "NS")
        except dns.exception.DNSException:
            try:
                return dns.resolver.Resolver()
            except dns.exception.DNSException:
                return resolver
        return resolver

    @staticmethod
    def _usable(response: dns.message.Message, zone: str) -> bool:
        if response.rcode() not in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN):
            return False
        return (
            response.rcode() == dns.rcode.NXDOMAIN
            or bool(response.flags & dns.flags.AA)
            or any(
                rrset.rdtype in (dns.rdatatype.NS, dns.rdatatype.SOA)
                and rrset.name.is_subdomain(dns.name.from_text(zone))
                for rrset in response.authority
            )
        )

    def zone_of(self, domain: str) -> str:
        try:
            response = self.resolver.resolve(domain, "SOA", raise_on_no_answer=False).response
        except dns.exception.DNSException:
            return domain.rstrip(".").lower()
        for rrset in (*response.answer, *response.authority):
            if rrset.rdtype == dns.rdatatype.SOA:
                return Referral.text(rrset.name)
        return domain.rstrip(".").lower()

    def _ask(self, zone: str, hosts: list[str]) -> Referral | None:
        for host in hosts:
            try:
                addresses = [record.to_text() for record in self.resolver.resolve(host, "A")]
            except dns.exception.DNSException:
                continue
            for address in addresses:
                try:
                    response = dns.query.udp(self.query, address, timeout=3)
                except (dns.exception.DNSException, OSError):
                    continue
                if self._usable(response, zone):
                    return Referral(response, self.fqdn, zone)
        return None

    def nameservers(self, zone: str) -> tuple[set[str] | None, Cut | None]:
        try:
            hosts = sorted(record.to_text() for record in self.resolver.resolve(zone, "NS"))
        except dns.exception.DNSException:
            return None, None
        referral = self._ask(zone, hosts)
        if referral is None:
            return None, None
        return referral.at_cell or set(), referral.cut_above_cell
