import pytest

from pulumi_pinecone_byoc.common import api
from pulumi_pinecone_byoc.common.api import PineconeApiError, create_environment

ENVIRONMENT = {
    "id": "env-1",
    "name": "aws-us-east-2-ab12.byoc",
    "org_id": "org-1",
    "org_name": "byoc",
}


def call(monkeypatch, response, domain, warnings):
    sent = {}

    def request(_method, _url, headers=None, body=None):
        sent.update(body or {})
        return response

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr(api.pulumi.log, "warn", warnings.append)
    environment = create_environment(
        "aws", "us-east-2", "ci", "https://api", "not-a-key", domain=domain
    )
    return sent, environment


def test_a_pinecone_hosted_cell_sends_no_domain(monkeypatch):
    sent, _ = call(monkeypatch, ENVIRONMENT, None, [])
    assert "domain" not in sent


def test_the_domain_the_control_plane_echoes_back_is_accepted(monkeypatch):
    warnings = []
    sent, environment = call(
        monkeypatch, {**ENVIRONMENT, "domain": "corp.example.com"}, "corp.example.com", warnings
    )
    assert sent["domain"] == "corp.example.com"
    assert environment.domain == "corp.example.com"
    assert warnings == []


def test_a_control_plane_that_records_no_domain_only_warns(monkeypatch):
    warnings = []
    _, environment = call(monkeypatch, ENVIRONMENT, "corp.example.com", warnings)
    assert environment.domain is None
    assert len(warnings) == 1
    assert "corp.example.com" in warnings[0]


def test_a_control_plane_that_records_another_domain_is_refused(monkeypatch):
    with pytest.raises(PineconeApiError, match="corp.example.com"):
        call(monkeypatch, {**ENVIRONMENT, "domain": "pinecone.io"}, "corp.example.com", [])
