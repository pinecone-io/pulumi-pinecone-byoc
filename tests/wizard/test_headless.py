import inspect
import re

import pytest
import wizard
from wizard import AWSSetupWizard, AzureSetupWizard, GCPSetupWizard, HeadlessInputRequired

CREDS = {
    AWSSetupWizard: "_validate_aws_creds",
    GCPSetupWizard: "_validate_gcp_creds",
    AzureSetupWizard: "_validate_azure_creds",
}


def gates_on(source: str, check: str) -> bool:
    stop = r"return (?:False|None)"
    direct = rf"if (?:[\w. ]+ and )?not self\.{check}\([^)]*\):\s*\n\s*{stop}"
    via_name = rf"(\w+) = self\.{check}\([^)]*\)\s*\n\s*if not \1:\s*\n\s*{stop}"
    return bool(re.search(direct, source) or re.search(via_name, source))


@pytest.mark.parametrize("cls", CREDS, ids=lambda c: c.__name__)
def test_run_refuses_to_continue_past_any_failed_check(cls):
    run = inspect.getsource(cls.run)
    source = run + inspect.getsource(cls._validated_api_key)

    assert "_run_headless" not in source
    assert "self._validated_api_key(output_dir)" in run

    for check in ("_validate_api_key", CREDS[cls], "_run_preflight_checks"):
        assert gates_on(source, check), f"{cls.__name__}.run must stop on a failed {check}"


def test_headless_never_reads_stdin():
    with pytest.raises(HeadlessInputRequired) as excinfo:
        AWSSetupWizard(headless=True)._prompt("Passphrase", password=True)

    assert excinfo.value.field == "Passphrase"


def test_every_step_the_wizard_prompts_for_is_answerable_in_headless():
    prompted = set(re.findall(r'key=f?"([^"]+)"', inspect.getsource(wizard)))
    computed = {k for k in prompted if "{" in k}

    assert computed == {"PINECONE_{name.upper()}"}

    unanswerable = {k for k in prompted - computed if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k)}
    assert unanswerable == set(), f"prompted but unanswerable: {sorted(unanswerable)}"
