"""Tests for the resumable-answer state used by the setup wizard."""

import json

import pytest
from wizard import StateKey, WizardState


@pytest.fixture
def state(tmp_path):
    return WizardState(str(tmp_path))


def test_new_state_is_empty(state):
    assert state.is_empty
    assert state.get(StateKey.REGION) == ""
    assert state.get(StateKey.REGION, "us-east-1") == "us-east-1"


def test_set_persists_to_disk(state, tmp_path):
    state.set(StateKey.REGION, "us-west-2")

    on_disk = json.loads((tmp_path / WizardState.FILENAME).read_text())
    assert on_disk == {"region": "us-west-2"}
    assert not state.is_empty


def test_load_reads_back_saved_answers(tmp_path):
    WizardState(str(tmp_path)).set(StateKey.CIDR, "10.5.0.0/16")

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get(StateKey.CIDR) == "10.5.0.0/16"


def test_get_returns_default_for_missing_key(state):
    state.set(StateKey.REGION, "eu-west-1")
    assert state.get(StateKey.PROJECT_NAME, "pinecone-byoc") == "pinecone-byoc"


def test_clear_removes_file_and_data(state, tmp_path):
    state.set(StateKey.REGION, "us-west-2")
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


def test_statekey_serializes_as_plain_string():
    # StrEnum members must round-trip through JSON as their string value so the
    # on-disk file stays human-readable and loads back with plain-string keys.
    assert StateKey.REGION == "region"
    assert json.dumps({StateKey.REGION: "x"}) == '{"region": "x"}'


def test_set_then_load_uses_plain_string_keys(tmp_path):
    # A value written via the enum must be retrievable after a fresh load,
    # proving enum keys and the loaded string keys are interchangeable.
    WizardState(str(tmp_path)).set(StateKey.AZS, "us-west-2a,us-west-2b")

    reloaded = WizardState(str(tmp_path)).load()
    assert reloaded.get(StateKey.AZS) == "us-west-2a,us-west-2b"
