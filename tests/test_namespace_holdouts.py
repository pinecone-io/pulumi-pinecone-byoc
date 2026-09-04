import json

from e2e import kube


def _namespace_returning(monkeypatch, payload):
    class Result:
        returncode = 0 if payload is not None else 1
        stdout = payload or ""
        stderr = ""

    monkeypatch.setattr(kube, "kubectl", lambda *a, **k: Result())


def test_a_terminating_namespace_reports_what_the_api_server_blames(monkeypatch):
    remaining = "Some resources are remaining: gateway.solo.io/v1, Kind=Gateway has 2 instances"
    _namespace_returning(
        monkeypatch,
        json.dumps(
            {
                "status": {
                    "conditions": [{"type": "NamespaceContentRemaining", "message": remaining}]
                }
            }
        ),
    )

    assert kube.namespace_holdouts("kubeconfig", "gloo-system") == [
        ("NamespaceContentRemaining", remaining)
    ]


def test_a_healthy_namespace_holds_nothing(monkeypatch):
    _namespace_returning(monkeypatch, json.dumps({"status": {}}))

    assert kube.namespace_holdouts("kubeconfig", "gloo-system") == []


def test_an_unreachable_namespace_holds_nothing_rather_than_raising(monkeypatch):
    _namespace_returning(monkeypatch, None)

    assert kube.namespace_holdouts("kubeconfig", "gloo-system") == []
