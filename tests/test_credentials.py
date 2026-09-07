"""The credential-name policy, and that every provider applies it."""

import re
from pathlib import Path

import pulumi

from pulumi_pinecone_byoc.common.credentials import SECRET_OUTPUTS, with_secret_outputs

PROVIDERS = Path(__file__).resolve().parent.parent / "pulumi_pinecone_byoc/common/providers.py"


def test_the_caller_keeps_their_own_options():
    merged = with_secret_outputs(pulumi.ResourceOptions(depends_on=[], protect=True))
    assert merged.additional_secret_outputs == SECRET_OUTPUTS
    assert merged.protect is True
    assert merged.depends_on == []


def test_a_caller_asking_for_more_secrets_is_not_overruled():
    merged = with_secret_outputs(pulumi.ResourceOptions(additional_secret_outputs=["mine"]))
    assert sorted(merged.additional_secret_outputs or []) == sorted([*SECRET_OUTPUTS, "mine"])


def test_no_options_at_all_still_gets_the_policy():
    assert with_secret_outputs(None).additional_secret_outputs == SECRET_OUTPUTS


# what each provider is known to hand back; `value` is the project's API key, and no
# name heuristic will ever guess that, which is why the list is written out
KNOWN_CREDENTIAL_OUTPUTS = {
    "api_key",
    "auth0_client_secret",
    "client_secret",
    "cpgw_api_key",
    "key",
    "pinecone_api_key",
    "value",
}


def test_the_credentials_we_know_about_are_in_the_policy():
    assert not sorted(KNOWN_CREDENTIAL_OUTPUTS - set(SECRET_OUTPUTS))


def test_a_newly_added_credential_shaped_property_must_be_declared():
    """Catches the ones nobody wrote down: an output called *_token or *_secret that
    a provider grows later. It cannot catch a name like `value` - that is what the
    list above is for."""
    source = PROVIDERS.read_text()
    returned = set(re.findall(r'"(\w+)":', source)) | set(
        re.findall(r'"(\w+)"\s*:\s*props', source)
    )
    credential_shaped = {
        name
        for name in returned
        if re.search(r"(^|_)(secret|key|token|password|credential)s?($|_)", name)
        and not name.endswith("_id")
        and not name.endswith("_arn")
        and name not in {"key_name", "keys"}
    }
    missing = sorted(credential_shaped - set(SECRET_OUTPUTS))
    assert not missing, f"credential-shaped provider properties not in SECRET_OUTPUTS: {missing}"


def test_every_dynamic_resource_applies_the_policy():
    source = PROVIDERS.read_text()
    supers = re.findall(r"super\(\).__init__\(\s*(\w+)Provider\(\),(.*?)\n        \)", source, re.S)
    assert supers, "no dynamic resources found; this test is looking in the wrong place"
    unguarded = [name for name, body in supers if "with_secret_outputs(opts)" not in body]
    assert not unguarded, f"these resources pass raw opts and will leak their outputs: {unguarded}"
