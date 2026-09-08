from e2e import delegations as sweep

APEX = "byoc.pinecone.io."


def record(name, *nameservers):
    return {
        "Name": f"{name}.byoc.pinecone.io.",
        "Type": "NS",
        "TTL": 300,
        "ResourceRecords": [{"Value": ns} for ns in nameservers],
    }


class Route53:
    def __init__(self, *records):
        self.records = [
            {"Name": APEX, "Type": "NS", "TTL": 172800, "ResourceRecords": []},
            {"Name": APEX, "Type": "SOA", "TTL": 900, "ResourceRecords": []},
            *records,
        ]
        self.deleted = []

    def get_hosted_zone(self, Id):
        return {"HostedZone": {"Name": APEX}}

    def get_paginator(self, name):
        records = self.records
        return type("P", (), {"paginate": lambda self, **_: [{"ResourceRecordSets": records}]})()

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        for change in ChangeBatch["Changes"]:
            assert change["Action"] == "DELETE"
            self.deleted.append(change["ResourceRecordSet"]["Name"])


def answers(**by_nameserver):
    return lambda zone, nameserver: by_nameserver[nameserver]


def test_one_authoritative_answer_keeps_a_delegation():
    ask = answers(a=sweep.DANGLING, b=sweep.ALIVE, c=sweep.UNSURE)
    assert sweep.verdict("x", ["a", "b", "c"], ask) == sweep.ALIVE


def test_every_nameserver_refusing_is_dangling():
    ask = answers(a=sweep.DANGLING, b=sweep.DANGLING)
    assert sweep.verdict("x", ["a", "b"], ask) == sweep.DANGLING


def test_an_unreachable_nameserver_is_not_a_refusal():
    ask = answers(a=sweep.DANGLING, b=sweep.UNSURE)
    assert sweep.verdict("x", ["a", "b"], ask) == sweep.UNSURE


def test_sweep_removes_only_the_dangling_and_never_the_apex():
    r53 = Route53(record("live", "ns-1.live"), record("gone", "ns-1.gone", "ns-2.gone"))
    ask = answers(
        **{"ns-1.live": sweep.ALIVE, "ns-1.gone": sweep.DANGLING, "ns-2.gone": sweep.DANGLING}
    )
    assert sweep.sweep(r53, "Z", limit=5, dry_run=False, ask=ask) == 0
    assert r53.deleted == ["gone.byoc.pinecone.io."]


def test_dry_run_removes_nothing():
    r53 = Route53(record("gone", "ns-1.gone"))
    assert (
        sweep.sweep(r53, "Z", limit=5, dry_run=True, ask=answers(**{"ns-1.gone": sweep.DANGLING}))
        == 0
    )
    assert r53.deleted == []


def test_an_undecided_delegation_stays_and_fails_the_run():
    r53 = Route53(record("gone", "ns-1.gone"), record("odd", "ns-1.odd"))
    ask = answers(**{"ns-1.gone": sweep.DANGLING, "ns-1.odd": sweep.UNSURE})
    assert sweep.sweep(r53, "Z", limit=5, dry_run=False, ask=ask) == 1
    assert r53.deleted == ["gone.byoc.pinecone.io."]


def test_more_dangling_than_the_limit_removes_none():
    r53 = Route53(*(record(f"gone{i}", f"ns.gone{i}") for i in range(3)))
    ask = answers(**{f"ns.gone{i}": sweep.DANGLING for i in range(3)})
    assert sweep.sweep(r53, "Z", limit=2, dry_run=False, ask=ask) == 1
    assert r53.deleted == []


def test_step_summary_is_written_when_github_asks(tmp_path, monkeypatch):
    out = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(out))
    r53 = Route53(record("gone", "ns-1.gone"))
    sweep.sweep(r53, "Z", limit=5, dry_run=True, ask=answers(**{"ns-1.gone": sweep.DANGLING}))
    text = out.read_text()
    assert "1 checked, would remove 1" in text
    assert "| dangling | `gone.byoc.pinecone.io.` |" in text
