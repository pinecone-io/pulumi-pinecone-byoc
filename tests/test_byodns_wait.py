import pytest

from pulumi_pinecone_byoc.common import providers
from pulumi_pinecone_byoc.common.providers import DelegatedZoneProvider

FQDN = "aws-us-east-2-ab12.byoc.corp.example.com"
OURS = ["ns-1.awsdns-01.org.", "ns-2.awsdns-02.co.uk."]


def create(monkeypatch, answers, wait_seconds=300):
    asked = []
    clock = [1_000_000.0]

    def resolve(fqdn):
        asked.append(fqdn)
        return answers[min(len(asked) - 1, len(answers) - 1)]

    monkeypatch.setattr(providers, "resolve_nameservers", resolve)
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
