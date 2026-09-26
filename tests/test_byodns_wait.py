import dns.rdata
import dns.resolver
import pytest

from pulumi_pinecone_byoc.common import api, providers
from pulumi_pinecone_byoc.common.providers import DelegatedZoneProvider

FQDN = "aws-us-east-2-ab12.byoc.corp.example.com"
OURS = ["ns-1.awsdns-01.org.", "ns-2.awsdns-02.co.uk."]
CHECKED: list[str] = []


def create(monkeypatch, answers, wait_seconds=300, parent_fails=(False,)):
    asked = []
    parents = CHECKED
    parents.clear()
    clock = [1_000_000.0]

    def resolve(fqdn):
        asked.append(fqdn)
        return answers[min(len(asked) - 1, len(answers) - 1)]

    def fails(fqdn):
        parents.append(fqdn)
        return parent_fails[min(len(parents) - 1, len(parent_fails) - 1)]

    monkeypatch.setattr(providers, "resolve_nameservers", resolve)
    monkeypatch.setattr(providers, "fails_to_resolve", fails)
    monkeypatch.setattr(providers.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    props = {"fqdn": FQDN, "nameservers": OURS, "wait_seconds": wait_seconds}
    return asked, DelegatedZoneProvider().create(props)


def test_a_delegated_zone_is_accepted_without_waiting(monkeypatch):
    asked, result = create(monkeypatch, [{"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}])
    assert asked == [FQDN]
    assert result.id == FQDN
    assert result.outs["nameservers_seen"] == ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"]


def test_a_zone_nobody_delegated_is_refused_with_the_records_to_add(monkeypatch):
    with pytest.raises(Exception) as raised:
        create(monkeypatch, [set()])
    message = str(raised.value)
    assert "does not resolve" in message
    for nameserver in OURS:
        assert f"{FQDN}.  NS  {nameserver}" in message
    assert "byoc.corp.example.com is served" in message


def test_a_zone_delegated_elsewhere_is_refused(monkeypatch):
    with pytest.raises(Exception, match="does not resolve"):
        create(monkeypatch, [{"ns-9.someone-else.net"}])


def test_records_placed_on_the_parent_are_refused_with_where_they_belong(monkeypatch):
    ours = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}
    with pytest.raises(Exception) as raised:
        create(monkeypatch, [ours], parent_fails=(True,))
    message = str(raised.value)
    assert "byoc.corp.example.com returns SERVFAIL" in message
    assert f"Move the NS records from byoc.corp.example.com to {FQDN}" in message
    for nameserver in OURS:
        assert f"{FQDN}.  NS  {nameserver}" in message


def test_every_name_above_the_cell_is_checked_but_not_the_tld(monkeypatch):
    ours = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}
    create(monkeypatch, [ours])
    assert CHECKED == [
        "byoc.corp.example.com",
        "corp.example.com",
        "example.com",
    ]


def test_a_failure_higher_up_is_named(monkeypatch):
    ours = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}
    with pytest.raises(Exception, match="corp.example.com returns SERVFAIL"):
        create(monkeypatch, [ours], parent_fails=(False, True))


def test_records_moved_off_the_parent_are_waited_for(monkeypatch):
    ours = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}
    asked, result = create(monkeypatch, [ours], parent_fails=(True, False))
    assert len(asked) == 2
    assert result.id == FQDN


def test_a_delegation_that_lands_late_is_waited_for(monkeypatch):
    answers = [set(), set(), {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}]
    asked, result = create(monkeypatch, answers)
    assert len(asked) == 3
    assert result.id == FQDN


def test_only_a_change_of_fqdn_replaces_the_wait():
    provider = DelegatedZoneProvider()
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": FQDN}).changes is False
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": "other." + FQDN}).changes is True
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": FQDN}).replaces == ["fqdn"]


def test_the_first_up_looks_once_and_stops(monkeypatch):
    asked, _ = None, None
    with pytest.raises(Exception, match="does not resolve"):
        asked, _ = create(monkeypatch, [set()], wait_seconds=0)
    assert asked is None


def test_the_first_up_asks_exactly_one_resolver(monkeypatch):
    calls = []

    def resolve(fqdn):
        calls.append(fqdn)
        return set()

    monkeypatch.setattr(providers, "resolve_nameservers", resolve)
    with pytest.raises(Exception, match="does not resolve"):
        DelegatedZoneProvider().create({"fqdn": FQDN, "nameservers": OURS, "wait_seconds": 0})
    assert calls == [FQDN]


def test_a_delegation_already_in_place_needs_no_wait(monkeypatch):
    asked, result = create(monkeypatch, [{"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}], 0)
    assert asked == [FQDN]
    assert result.id == FQDN


def test_the_attempt_counter_starts_at_one_and_climbs():
    provider = providers.DelegationAttemptProvider()
    news = {"fqdn": FQDN}

    first = dict(provider.create(news).outs or {})
    assert first["attempts"] == 1
    assert provider.diff("id", first, news).changes is True

    second = dict(provider.update("id", first, news).outs or {})
    assert second["attempts"] == 2
    assert dict(provider.update("id", second, news).outs or {})["attempts"] == 3


@pytest.mark.parametrize(
    "raised, fails",
    [
        (None, False),
        (dns.resolver.NXDOMAIN, False),
        (dns.resolver.NoNameservers, True),
        (dns.resolver.LifetimeTimeout, True),
    ],
)
def test_only_servfail_or_silence_counts_as_failing(monkeypatch, raised, fails):
    def resolve(*_args, **_kwargs):
        if raised:
            raise raised()
        return []

    monkeypatch.setattr(api.dns.resolver, "resolve", resolve)
    assert api.fails_to_resolve("byoc.corp.example.com") is fails


def test_nameservers_come_back_normalised(monkeypatch):
    records = [
        dns.rdata.from_text("IN", "NS", n) for n in ["NS-1.awsdns-01.org.", "ns-2.awsdns-02.co.uk."]
    ]
    monkeypatch.setattr(api.dns.resolver, "resolve", lambda *_a, **_k: records)
    assert api.resolve_nameservers(FQDN) == {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}


def test_a_name_that_does_not_resolve_has_no_nameservers(monkeypatch):
    def resolve(*_args, **_kwargs):
        raise dns.resolver.NoNameservers()

    monkeypatch.setattr(api.dns.resolver, "resolve", resolve)
    assert api.resolve_nameservers(FQDN) == set()
