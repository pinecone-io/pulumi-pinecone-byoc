import pytest

from pulumi_pinecone_byoc.common import providers
from pulumi_pinecone_byoc.common.providers import DelegatedZoneProvider

DOMAIN = "corp.example.com"
FQDN = "aws-us-east-2-ab12.byoc.corp.example.com"
OURS = ["ns-1.awsdns-01.org.", "ns-2.awsdns-02.co.uk."]
WANTED = {"ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"}


def create(monkeypatch, answers, wait_seconds=300):
    asked = []
    logged = []
    clock = [1_000_000.0]

    def delegated(check, zone):
        asked.append((check.fqdn, zone, check.resolver))
        answer = answers[min(len(asked) - 1, len(answers) - 1)]
        return answer if isinstance(answer, tuple) else (answer, None)

    check = providers.DelegationCheck
    monkeypatch.setattr(check, "public_resolver", staticmethod(lambda: "resolver"))
    monkeypatch.setattr(check, "zone_of", lambda self, domain: domain)
    monkeypatch.setattr(check, "nameservers", delegated)
    monkeypatch.setattr(providers.pulumi.log, "info", logged.append)
    monkeypatch.setattr(providers.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    props = {"fqdn": FQDN, "domain": DOMAIN, "nameservers": OURS, "wait_seconds": wait_seconds}
    return asked, logged, DelegatedZoneProvider().create(props)


def refused(monkeypatch, answers, **kwargs):
    with pytest.raises(Exception) as raised:
        create(monkeypatch, answers, **kwargs)
    return str(raised.value)


def test_a_delegated_zone_is_accepted_without_waiting(monkeypatch):
    asked, _, result = create(monkeypatch, [WANTED])
    assert asked == [(FQDN, DOMAIN, "resolver")]
    assert result.id == FQDN
    assert result.outs["nameservers_seen"] == ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"]


def test_a_zone_nobody_delegated_is_refused_with_the_zone_and_record_to_add(monkeypatch):
    message = refused(monkeypatch, [set()])
    assert (
        "In the `corp.example.com` zone, create an NS record named "
        "`aws-us-east-2-ab12.byoc` with these values:"
    ) in message
    for nameserver in WANTED:
        assert f"    {FQDN}.  NS  {nameserver}." in message


def test_a_zone_delegated_elsewhere_is_refused(monkeypatch):
    message = refused(monkeypatch, [{"ns-9.someone-else.net"}])
    assert "currently say ['ns-9.someone-else.net'] serves it" in message


def test_unreachable_nameservers_are_never_taken_as_delegated(monkeypatch):
    message = refused(monkeypatch, [None])
    assert "No nameserver of `corp.example.com` gave a usable answer from here" in message
    assert f"the delegation of `{FQDN}` cannot be verified" in message


def test_records_on_byoc_say_why_the_cell_stays_hidden(monkeypatch):
    above = ("byoc.corp.example.com", WANTED)
    message = refused(monkeypatch, [(set(), above)])
    assert "create an NS record named `aws-us-east-2-ab12.byoc`" in message
    assert (
        "`corp.example.com` delegates `byoc.corp.example.com` to "
        "['ns-1.awsdns-01.org', 'ns-2.awsdns-02.co.uk']; records below it are hidden. "
        "Remove that NS record from `corp.example.com`; it was meant for this cell."
    ) in message
    assert "add the cell's record" not in message
    assert "serves it" not in message


def test_a_byoc_zone_of_their_own_is_refused_as_unsupported(monkeypatch):
    above = ("byoc.corp.example.com", {"ns1.corp-dns.net"})
    message = refused(monkeypatch, [(set(), above)])
    assert (
        "A separate `byoc.corp.example.com` zone is not supported: the cell's NS record "
        "must be in the `corp.example.com` zone."
    ) in message
    assert "Remove that NS record" not in message


def test_a_delegation_that_lands_late_is_waited_for_and_asked_for_once(monkeypatch):
    asked, logged, result = create(monkeypatch, [set(), set(), None, WANTED])
    assert len(asked) == 4
    assert result.id == FQDN
    assert len(logged) == 2
    assert "create an NS record" in logged[0]
    assert "No nameserver" in logged[1]


def test_only_a_change_of_fqdn_replaces_the_wait():
    provider = DelegatedZoneProvider()
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": FQDN}).changes is False
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": "other." + FQDN}).changes is True
    assert provider.diff("id", {"fqdn": FQDN}, {"fqdn": FQDN}).replaces == ["fqdn"]


def test_the_first_up_looks_once_and_stops(monkeypatch):
    def sleep(_seconds):
        raise AssertionError("the first up must not wait")

    monkeypatch.setattr(providers.time, "sleep", sleep)
    with pytest.raises(Exception, match="create an NS record"):
        create(monkeypatch, [set()], wait_seconds=0)


def test_the_attempt_counter_starts_at_one_and_climbs():
    provider = providers.DelegationAttemptProvider()
    news = {"fqdn": FQDN}

    first = dict(provider.create(news).outs or {})
    assert first["attempts"] == 1
    assert provider.diff("id", first, news).changes is True

    second = dict(provider.update("id", first, news).outs or {})
    assert second["attempts"] == 2
    assert dict(provider.update("id", second, news).outs or {})["attempts"] == 3
