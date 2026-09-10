"""What the GCP wizard takes for the credentials and the project it deploys with.

The project is the wizard's to ask for, not the environment's to supply, and
gcloud will only take it as an ID - every `--project=` in the preflight checks
refuses the number a project number is. Credentials are proven by a refresh,
whose token is discarded by the kernel rather than read in here, so the fake
below answers a return code and nothing else.
"""

import subprocess

import pytest
import wizard
from wizard import GCPSetupWizard, NonInteractiveInputRequired

NUMBER = "740000000000"
UNKNOWN_NUMBER = "999999999999"
PROJECT_ID = "a-project"

TOKEN = ["gcloud", "auth", "print-access-token"]
ADC_TOKEN = ["gcloud", "auth", "application-default", "print-access-token"]


class Gcloud:
    """gcloud, logged in, with no project configured - the case the wizard used to refuse."""

    def __init__(self):
        self.authenticated = True
        self.adc = True
        self.configured = ""
        self.projects = {NUMBER: PROJECT_ID}

    def __call__(self, args, **kwargs):
        args = list(args)
        if args == TOKEN:
            return self._answer(args, "", ok=self.authenticated)
        if args == ADC_TOKEN:
            return self._answer(args, "", ok=self.adc)
        if args[:4] == ["gcloud", "config", "get-value", "project"]:
            return self._answer(args, self.configured)
        if args[:3] == ["gcloud", "projects", "describe"]:
            project_id = self.projects.get(args[3], "")
            return self._answer(args, project_id, ok=bool(project_id))
        return self._answer(args, "")  # the auth plugin, and anything else

    @staticmethod
    def _answer(args, stdout, ok=True):
        return subprocess.CompletedProcess(args, 0 if ok else 1, stdout=stdout, stderr="")


@pytest.fixture
def gcloud(monkeypatch):
    fake = Gcloud()
    monkeypatch.setattr(wizard.subprocess, "run", fake)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    for name in GCPSetupWizard.PROJECT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return fake


@pytest.fixture
def gcp(gcloud, monkeypatch):
    monkeypatch.setattr(wizard.shutil, "which", lambda _: "/usr/bin/gcloud")
    return GCPSetupWizard(non_interactive=True)


def test_credentials_are_valid_without_a_project_to_deploy_into(gcp):
    assert gcp._validate_gcp_creds() is True, "the step after this one is what asks for a project"


def test_credentials_gcloud_cannot_refresh_are_refused(gcp, gcloud):
    gcloud.authenticated = False

    assert gcp._validate_gcp_creds() is False, "the preflight checks shell out to gcloud"


def test_credentials_are_refused_when_only_adc_is_missing(gcp, gcloud):
    gcloud.adc = False

    assert gcp._validate_gcp_creds() is False, "pulumi authenticates with ADC, not with gcloud"


def test_the_token_a_refresh_returns_is_never_read(gcp, monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen[tuple(args)] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=None, stderr=None)

    monkeypatch.setattr(wizard.subprocess, "run", run)
    gcp._refresh_succeeds(*TOKEN)

    assert seen[tuple(TOKEN)] == {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }, "a captured token is a token this process is holding"


def test_no_project_anywhere_names_the_variable_that_would_answer_it(gcp):
    with pytest.raises(NonInteractiveInputRequired) as excinfo:
        gcp._get_project_id()

    assert excinfo.value.env_var == "GCP_PROJECT"


def test_a_project_number_is_answered_with_the_project_id(gcp, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", NUMBER)

    assert gcp._get_project_id() == PROJECT_ID


def test_a_number_no_project_answers_to_is_refused_rather_than_passed_on(gcp, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", UNKNOWN_NUMBER)

    with pytest.raises(NonInteractiveInputRequired):
        gcp._get_project_id()


def test_a_project_id_is_taken_as_given(gcp, monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", PROJECT_ID)

    assert gcp._get_project_id() == PROJECT_ID


@pytest.mark.parametrize("name", GCPSetupWizard.PROJECT_ENV_VARS)
def test_a_project_named_in_the_environment_is_offered_as_an_id(gcp, monkeypatch, name):
    monkeypatch.setenv(name, NUMBER)

    assert gcp._detected_project() == PROJECT_ID


def test_the_environment_outranks_the_project_gcloud_has_configured(gcp, gcloud, monkeypatch):
    gcloud.configured = "some-other-project"
    monkeypatch.setenv("CLOUDSDK_CORE_PROJECT", PROJECT_ID)

    assert gcp._detected_project() == PROJECT_ID, "gcloud reads it in that order too"


def test_a_number_gcloud_has_configured_is_resolved_before_it_is_offered(gcp, gcloud):
    gcloud.configured = NUMBER

    assert gcp._detected_project() == PROJECT_ID


def test_the_project_gcloud_has_configured_is_offered(gcp, gcloud):
    gcloud.configured = PROJECT_ID

    assert gcp._detected_project() == PROJECT_ID


def test_a_project_nothing_can_resolve_is_not_offered(gcp, gcloud):
    gcloud.configured = UNKNOWN_NUMBER

    assert gcp._detected_project() == "", "a number the prompt cannot use is worse than no default"


def test_a_project_the_environment_names_but_nothing_resolves_falls_through(
    gcp, gcloud, monkeypatch
):
    """A CI runner exports the number of a project this account cannot read."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", UNKNOWN_NUMBER)
    gcloud.configured = PROJECT_ID

    assert gcp._detected_project() == PROJECT_ID, "gcloud knew the answer all along"


def test_a_missing_gcloud_is_reported_as_itself(gcp, monkeypatch):
    monkeypatch.setattr(wizard.shutil, "which", lambda _: None)

    assert gcp._validate_gcp_creds() is False, "and not as a credential the user must fix"


def test_a_gcloud_that_is_not_installed_raises_nothing(gcp, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gcloud")

    monkeypatch.setattr(wizard.subprocess, "run", missing)

    assert gcp._refresh_succeeds(*TOKEN) is False
    assert gcp._detected_project() == ""
    assert gcp._project_id_for(NUMBER) == ""


def test_an_answer_the_step_refused_is_not_offered_back(gcp, monkeypatch, tmp_path):
    gcp._state = wizard.WizardState(str(tmp_path))
    gcp._state.set("GCP_PROJECT", "stale")
    monkeypatch.setenv("GCP_PROJECT", UNKNOWN_NUMBER)

    with pytest.raises(NonInteractiveInputRequired):
        gcp._get_project_id()

    assert gcp._state.get("GCP_PROJECT") == "", "a resumed run would pre-fill the bad number"


def test_the_id_is_what_a_resumed_run_offers_back(gcp, monkeypatch, tmp_path):
    gcp._state = wizard.WizardState(str(tmp_path))
    monkeypatch.setenv("GCP_PROJECT", NUMBER)

    assert gcp._get_project_id() == PROJECT_ID
    assert gcp._state.get("GCP_PROJECT") == PROJECT_ID, "the number was only how it was reached"
