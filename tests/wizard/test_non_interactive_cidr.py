"""What CI gets when it asks for a CIDR by name rather than by value."""

import pytest
from wizard import AWSSetupWizard, AzureSetupWizard, GCPSetupWizard, NonInteractiveInputRequired

EVERY_CLOUD = (AWSSetupWizard, GCPSetupWizard, AzureSetupWizard)


@pytest.mark.parametrize("wizard", EVERY_CLOUD, ids=lambda w: w.__name__)
def test_the_sentinel_resolves_to_the_wizards_own_default(monkeypatch, wizard):
    monkeypatch.setenv("PINECONE_VPC_CIDR", "default")
    assert wizard(non_interactive=True)._non_interactive_cidr() == wizard.DEFAULT_CIDR


def test_an_unset_variable_is_refused_rather_than_defaulted(monkeypatch):
    monkeypatch.delenv("PINECONE_VPC_CIDR", raising=False)
    assert AWSSetupWizard(non_interactive=True)._non_interactive_cidr() is None


def test_an_explicit_range_still_wins(monkeypatch):
    monkeypatch.setenv("PINECONE_VPC_CIDR", "172.16.32.0/20")
    assert AWSSetupWizard(non_interactive=True)._non_interactive_cidr() == "172.16.32.0/20"


@pytest.mark.parametrize("wizard", EVERY_CLOUD, ids=lambda w: w.__name__)
def test_the_sentinel_reaches_the_run_path_not_just_the_helper(monkeypatch, wizard):
    monkeypatch.setenv("PINECONE_VPC_CIDR", "default")
    assert wizard(non_interactive=True)._get_cidr() == wizard.DEFAULT_CIDR


def test_non_interactive_aws_refuses_an_unset_variable_rather_than_defaulting(monkeypatch):
    monkeypatch.delenv("PINECONE_VPC_CIDR", raising=False)

    with pytest.raises(NonInteractiveInputRequired) as excinfo:
        AWSSetupWizard(non_interactive=True)._get_cidr()

    assert excinfo.value.env_var == "PINECONE_VPC_CIDR"


@pytest.mark.parametrize("wizard", (GCPSetupWizard, AzureSetupWizard), ids=lambda w: w.__name__)
def test_non_interactive_gcp_and_azure_still_fall_back_to_their_own_default(monkeypatch, wizard):
    monkeypatch.delenv("PINECONE_VPC_CIDR", raising=False)
    assert wizard(non_interactive=True)._get_cidr() == wizard.DEFAULT_CIDR
