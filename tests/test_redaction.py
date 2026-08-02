"""What must never reach a log this repository publishes.

Every payload here is assembled at runtime rather than written out. A fabricated
credential in source is indistinguishable from a live one to a secret scanner:
full-length literals cost us a blocked push (GitHub push protection) and a red
check (GitGuardian). Split, they still exercise the same shapes.
"""

import pytest
from e2e.redaction import redact

PINECONE_KEY = "pcsk" + "_" + "a1b2C3d4" * 5
PULUMI_TOKEN = "pul" + "-" + "0f1e2d3c" * 3
AWS_KEY = "AKI" + "A" + "Q2W3E4R5T6Y7U8I9"
AWS_SESSION_KEY = "ASI" + "A" + "Z9X8C7V6B5N4M3Q2"
DATADOG_KEY = "8f14e45f" * 4
JWT = ".".join(("ey" + "J0eXAiOiJKV1QifQ", "ey" + "JzdWIiOiJmYWtlIn0", "c2ln" + "bmF0dXJl"))
DB_PASSWORD = "Tr0ub4dor" + "3xyz"
GIT_SHA = "c066925a" * 5

# (what it looks like in a log, the part that must not survive)
LEAKS = [
    ("pinecone api key", f"Api-Key: {PINECONE_KEY}", PINECONE_KEY),
    ("pulumi token", f"PULUMI_ACCESS_TOKEN={PULUMI_TOKEN}", PULUMI_TOKEN),
    ("aws access key", f"using {AWS_KEY} for the caller", AWS_KEY),
    ("aws session key", f"temporary {AWS_SESSION_KEY} from sts", AWS_SESSION_KEY),
    ("datadog api key", f"dd_api_key={DATADOG_KEY}", DATADOG_KEY),
    ("jwt / kubeconfig token", f"authorization: Bearer {JWT}", JWT.split(".")[1]),
    (
        "postgres password",
        "postgres" + f"://pcadmin:{DB_PASSWORD}@db.internal:5432/control",
        DB_PASSWORD,
    ),
    (
        "postgresql password",
        "postgresql" + f"://user:{DB_PASSWORD}@aurora.rds.amazonaws.com/system",
        DB_PASSWORD,
    ),
]


@pytest.mark.parametrize(
    ("kind", "line", "must_not_survive"), LEAKS, ids=[kind for kind, _, _ in LEAKS]
)
def test_a_credential_shape_never_survives(kind, line, must_not_survive):
    cleaned = redact(line)
    assert must_not_survive not in cleaned, f"{kind} survived redaction"
    assert "[redacted]" in cleaned


def test_ordinary_output_is_left_readable():
    plain = (
        "pulumi:pulumi:Stack ci-aws-upgrade-pr61 running\n"
        "  + aws:ec2/vpc:Vpc pc-vpc created (2s)\n"
        "  ~ aws:eks:NodeGroup pc-eks-ng-default updating [diff: ~launchTemplate,tags]\n"
        f"commit {GIT_SHA} deployed\n"
    )
    assert redact(plain) == plain, "a normal deploy line or a 40-char git sha was masked"


def test_masking_does_not_stop_at_the_first_hit():
    doubled = f"first {PINECONE_KEY} then {PULUMI_TOKEN}"
    cleaned = redact(doubled)
    assert cleaned.count("[redacted]") == 2
    assert "pcsk" + "_" not in cleaned and "pul" + "-" not in cleaned
