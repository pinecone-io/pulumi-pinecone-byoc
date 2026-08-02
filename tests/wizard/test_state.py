"""Tests for the resumable-answer state used by the setup wizard."""

import json

import pytest
from wizard import BaseSetupWizard, WizardState


class _StubWizard(BaseSetupWizard):
    CLOUD_NAME = "AWS"


@pytest.fixture
def state(tmp_path):
    return WizardState(str(tmp_path))


def test_new_state_is_empty(state):
    assert state.is_empty
    assert state.get("region") == ""
    assert state.get("region", "us-east-1") == "us-east-1"


def test_set_persists_to_disk(state, tmp_path):
    state.set("region", "us-west-2")

    on_disk = json.loads((tmp_path / WizardState.FILENAME).read_text())
    assert on_disk == {"region": "us-west-2"}
    assert not state.is_empty


def test_load_reads_back_saved_answers(tmp_path):
    WizardState(str(tmp_path)).set("cidr", "10.5.0.0/16")

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get("cidr") == "10.5.0.0/16"


def test_get_returns_default_for_missing_key(state):
    state.set("region", "eu-west-1")
    assert state.get("project_name", "pinecone-byoc") == "pinecone-byoc"


def test_clear_removes_file_and_data(state, tmp_path):
    state.set("region", "us-west-2")
    state.clear()

    assert state.is_empty
    assert not (tmp_path / WizardState.FILENAME).exists()


def test_clear_is_safe_when_no_file(state):
    # never persisted anything — clear must not raise
    state.clear()
    assert state.is_empty


def test_load_tolerates_missing_file(tmp_path):
    assert WizardState(str(tmp_path)).load().is_empty


def test_load_tolerates_corrupt_file(tmp_path):
    (tmp_path / WizardState.FILENAME).write_text("{ not valid json")
    assert WizardState(str(tmp_path)).load().is_empty


def test_load_tolerates_non_dict_json(tmp_path):
    (tmp_path / WizardState.FILENAME).write_text("[1, 2, 3]")
    assert WizardState(str(tmp_path)).load().is_empty


def test_set_then_load_round_trips(tmp_path):
    WizardState(str(tmp_path)).set("azs", "us-west-2a,us-west-2b")

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get("azs") == "us-west-2a,us-west-2b"


def test_resume_records_cloud_for_fresh_state(tmp_path):
    _StubWizard()._maybe_resume(str(tmp_path))

    assert WizardState(str(tmp_path)).load().get("cloud") == "AWS"


def test_resume_exits_on_a_different_cloud_with_progress(tmp_path):
    prior = WizardState(str(tmp_path))
    prior.set("cloud", "GCP")
    prior.set("region", "us-central1")

    with pytest.raises(SystemExit) as exc:
        _StubWizard()._maybe_resume(str(tmp_path))
    assert exc.value.code == 1

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get("cloud") == "GCP"
    assert reloaded.get("region") == "us-central1"


def test_resume_starts_fresh_on_marker_only_same_cloud(tmp_path, monkeypatch):
    WizardState(str(tmp_path)).set("cloud", "AWS")

    def _no_prompt(*a, **k):
        raise AssertionError("should not prompt for a marker-only checkpoint")

    monkeypatch.setattr("wizard.read_input_with_placeholder", _no_prompt)
    _StubWizard()._maybe_resume(str(tmp_path))

    assert WizardState(str(tmp_path)).load().stored_keys() == ["cloud"]


def test_resume_starts_fresh_on_marker_only_different_cloud(tmp_path, monkeypatch):
    WizardState(str(tmp_path)).set("cloud", "GCP")

    def _no_prompt(*a, **k):
        raise AssertionError("should not prompt for a marker-only checkpoint")

    monkeypatch.setattr("wizard.read_input_with_placeholder", _no_prompt)
    _StubWizard()._maybe_resume(str(tmp_path))

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get("cloud") == "AWS"
    assert reloaded.stored_keys() == ["cloud"]


def test_unset_removes_key_and_persists(tmp_path):
    state = WizardState(str(tmp_path))
    state.set("region", "us-west-2")
    state.set("azs", "us-west-2a,us-west-2b")

    state.unset("azs")

    on_disk = json.loads((tmp_path / WizardState.FILENAME).read_text())
    assert on_disk == {"region": "us-west-2"}


def test_unset_is_safe_for_missing_key(state):
    state.unset("never-set")
    assert state.is_empty


def test_zone_default_drops_stale_checkpoint(tmp_path):
    prior = WizardState(str(tmp_path))
    prior.set("azs", "us-east-1a,us-east-1b")

    wizard = _StubWizard()
    wizard._state = WizardState(str(tmp_path)).load()
    available = ["us-west-2a", "us-west-2b", "us-west-2c"]

    default = wizard._zone_default("azs", available)

    assert default == "us-west-2a,us-west-2b"
    assert wizard._state.get("azs") == ""


def test_zone_default_keeps_valid_checkpoint(tmp_path):
    prior = WizardState(str(tmp_path))
    prior.set("azs", "us-west-2b,us-west-2c")

    wizard = _StubWizard()
    wizard._state = WizardState(str(tmp_path)).load()
    available = ["us-west-2a", "us-west-2b", "us-west-2c"]

    default = wizard._zone_default("azs", available)

    assert default == "us-west-2a,us-west-2b"
    assert wizard._state.get("azs") == "us-west-2b,us-west-2c"


def test_resume_keeps_matching_cloud_state(tmp_path, monkeypatch):
    prior = WizardState(str(tmp_path))
    prior.set("cloud", "AWS")
    prior.set("region", "us-west-2")

    monkeypatch.setattr("wizard.read_input_with_placeholder", lambda *a, **k: "Y")
    _StubWizard()._maybe_resume(str(tmp_path))

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get("cloud") == "AWS"
    assert reloaded.get("region") == "us-west-2"
