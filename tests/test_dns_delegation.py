import types

import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rdata
import dns.resolver
import dns.rrset

from pulumi_pinecone_byoc.common import dns_delegation
from pulumi_pinecone_byoc.common.dns_delegation import DelegationCheck

DOMAIN = "corp.example.com"
FQDN = "aws-us-east-2-ab12.byoc.corp.example.com"
WANTED = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}


RECORDS = {
    ("corp.example.com", "NS"): ["ns-a.corp.example.com."],
    ("ns-a.corp.example.com", "A"): ["192.0.2.1", "192.0.2.2"],
}


class Resolver(dns.resolver.Resolver):
    def __init__(self):
        super().__init__(configure=False)

    def resolve(self, qname, rdtype="A", *_args, **_kwargs):
        name = str(qname).rstrip(".")
        if rdtype == "SOA":
            response = dns.message.make_response(dns.message.make_query(name, "SOA"))
            soa = "ns-a. h. 1 7200 900 1209600 86400"
            response.authority.append(dns.rrset.from_text(f"{DOMAIN}.", 900, "IN", "SOA", soa))
            return types.SimpleNamespace(response=response)
        return [dns.rdata.from_text("IN", rdtype, value) for value in RECORDS[(name, rdtype)]]


def referral(owner, *nameservers, rcode=dns.rcode.NOERROR):
    def answer(query):
        response = dns.message.make_response(query)
        response.set_rcode(rcode)
        if owner:
            response.authority.append(
                dns.rrset.from_text(f"{owner}.", 300, "IN", "NS", *[f"{n}." for n in nameservers])
            )
        return response

    return answer


def delegated(monkeypatch, by_address):
    asked = []

    def udp(query, where, timeout):
        asked.append(where)
        assert not query.flags & dns.flags.RD
        answer = by_address[where]
        if isinstance(answer, Exception):
            raise answer
        return answer(query)

    monkeypatch.setattr(dns_delegation.dns.query, "udp", udp)
    return asked, DelegationCheck(FQDN, Resolver()).nameservers(DOMAIN)


def test_the_zone_is_the_owner_of_the_domain_soa():
    assert DelegationCheck(FQDN, Resolver()).zone_of("dev.corp.example.com") == DOMAIN


def test_a_domain_whose_soa_fails_is_its_own_zone():
    class Failing(Resolver):
        def resolve(self, *_args, **_kwargs):
            raise dns.resolver.NoNameservers()

    assert DelegationCheck(FQDN, Failing()).zone_of("Corp.Example.com.") == DOMAIN


def test_a_referral_at_the_cell_is_its_delegation(monkeypatch):
    _, found = delegated(monkeypatch, {"192.0.2.1": referral(FQDN, *WANTED)})
    assert found == (WANTED, None)


def test_records_on_byoc_do_not_delegate_the_cell(monkeypatch):
    byoc = referral("byoc.corp.example.com", *WANTED)
    _, found = delegated(monkeypatch, {"192.0.2.1": byoc})
    assert found == (set(), ("byoc.corp.example.com", WANTED))


def test_a_byoc_zone_of_their_own_is_a_cut_and_never_followed(monkeypatch):
    byoc = referral("byoc.corp.example.com", "ns1.corp-dns.net")
    asked, found = delegated(monkeypatch, {"192.0.2.1": byoc})
    assert found == (set(), ("byoc.corp.example.com", {"ns1.corp-dns.net"}))
    assert asked == ["192.0.2.1"]


def test_an_authoritative_refusal_is_skipped_for_the_next(monkeypatch):
    def refusing(query):
        response = referral(None, rcode=dns.rcode.REFUSED)(query)
        response.flags |= dns.flags.AA
        return response

    asked, found = delegated(
        monkeypatch, {"192.0.2.1": refusing, "192.0.2.2": referral(FQDN, *WANTED)}
    )
    assert found == (WANTED, None)
    assert asked == ["192.0.2.1", "192.0.2.2"]


def test_a_name_the_zone_does_not_have_has_no_delegation(monkeypatch):
    _, found = delegated(monkeypatch, {"192.0.2.1": referral(None, rcode=dns.rcode.NXDOMAIN)})
    assert found == (set(), None)


def test_a_server_that_refuses_is_silent_or_lame_is_skipped_for_the_next(monkeypatch):
    lame = referral(None)
    for first in [dns.exception.Timeout(), referral(None, rcode=dns.rcode.REFUSED), lame]:
        asked, found = delegated(
            monkeypatch, {"192.0.2.1": first, "192.0.2.2": referral(FQDN, *WANTED)}
        )
        assert found == (WANTED, None)
        assert asked == ["192.0.2.1", "192.0.2.2"]


def test_no_usable_answer_is_none_not_empty(monkeypatch):
    _, found = delegated(
        monkeypatch,
        {
            "192.0.2.1": OSError("unreachable"),
            "192.0.2.2": referral(None, rcode=dns.rcode.SERVFAIL),
        },
    )
    assert found == (None, None)


def test_only_the_zone_nameservers_are_asked(monkeypatch):
    asked, _ = delegated(monkeypatch, {"192.0.2.1": referral("byoc.corp.example.com", *WANTED)})
    assert asked == ["192.0.2.1"]


def test_the_public_resolvers_are_probed_with_the_root_only(monkeypatch):
    probed = []
    monkeypatch.setattr(
        dns_delegation.dns.resolver.Resolver, "resolve", lambda self, *a, **_k: probed.append(a)
    )
    resolver = DelegationCheck.public_resolver()
    assert resolver.nameservers == ["1.1.1.1", "8.8.8.8"]
    assert probed == [(".", "NS")]


def test_blocked_port_53_falls_back_to_the_system_resolver(monkeypatch):
    probed = []

    def resolve(self, *args, **_kwargs):
        probed.append((self, args))
        raise dns.resolver.LifetimeTimeout(timeout=5, errors=[])

    monkeypatch.setattr(dns_delegation.dns.resolver.Resolver, "resolve", resolve)
    resolver = DelegationCheck.public_resolver()
    ((public, asked),) = probed
    assert public.nameservers == ["1.1.1.1", "8.8.8.8"]
    assert asked == (".", "NS")
    assert resolver is not public


def test_the_cut_closest_to_the_cell_is_the_one_reported():
    response = dns.message.make_response(dns.message.make_query(FQDN, "NS"))
    for owner, target in [("byoc.corp.example.com.", "ns-far."), (f"{FQDN}.", "ns-cell.")]:
        response.authority.append(dns.rrset.from_text(owner, 300, "IN", "NS", target))
    deep = "x.aws-us-east-2-ab12.byoc.corp.example.com"
    referral = dns_delegation.Referral(response, deep, DOMAIN)
    assert referral.at_cell is None
    assert referral.cut_above_cell == (FQDN, {"ns-cell"})
