"""What CI gets when it asks for a CIDR by name rather than by value."""

import pytest
from wizard import AWSSetupWizard, AzureSetupWizard, GCPSetupWizard

EVERY_CLOUD = (AWSSetupWizard, GCPSetupWizard, AzureSetupWizard)


@pytest.mark.parametrize("wizard", EVERY_CLOUD, ids=lambda w: w.__name__)
def test_the_sentinel_resolves_to_the_wizards_own_default(monkeypatch, wizard):
    monkeypatch.setenv("PINECONE_VPC_CIDR", "default")
    assert wizard(headless=True)._headless_cidr() == wizard.DEFAULT_CIDR


def test_an_unset_variable_is_refused_rather_than_defaulted(monkeypatch):
    monkeypatch.delenv("PINECONE_VPC_CIDR", raising=False)
    assert AWSSetupWizard(headless=True)._headless_cidr() is None


def test_an_explicit_range_still_wins(monkeypatch):
    monkeypatch.setenv("PINECONE_VPC_CIDR", "172.16.32.0/20")
    assert AWSSetupWizard(headless=True)._headless_cidr() == "172.16.32.0/20"
