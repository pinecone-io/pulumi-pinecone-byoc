"""What the provider is told, beyond which subscription to build in.

A default provider reads azure-native stack config; the explicit one this module
creates reads nothing but its own arguments. So a stack that authenticates the
documented Pulumi way - a service principal in config, or a sovereign cloud -
has to have that handed over, or the cell is built by whoever is logged in,
against the commercial cloud.
"""

import inspect

import pulumi
import pytest
from pulumi_azure_native import ProviderArgs

from pulumi_pinecone_byoc.azure import cluster

OURS = "11111111-2222-3333-4444-555555555555"
THEIRS = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def stack_config():
    """Stack config, as `pulumi config set azure-native:<key>` would leave it."""

    def configured(**settings):
        pulumi.runtime.set_all_config({f"azure-native:{k}": v for k, v in settings.items()})

    yield configured
    pulumi.runtime.set_all_config({})


def test_every_setting_the_provider_takes_is_either_forwarded_or_pinned():
    """When azure-native grows a setting, this fails rather than quietly dropping it."""
    takes = {
        _camel(name)
        for name in inspect.signature(ProviderArgs.__init__).parameters
        if name != "__self__"
    }
    handled = {
        *cluster.PROVIDER_SETTINGS,
        *cluster.PROVIDER_SECRETS,
        *cluster.PROVIDER_FLAGS,
        *cluster.PROVIDER_LISTS,
        *cluster.PROVIDER_PINNED,
    }

    assert takes == handled, "an azure-native setting nobody decided about"


def test_the_service_principal_in_config_reaches_the_provider(stack_config):
    stack_config(tenantId="a-tenant", clientId="a-client", useOidc="true")

    settings = cluster._provider_settings()

    assert settings["tenant_id"] == "a-tenant"
    assert settings["client_id"] == "a-client"
    assert settings["use_oidc"] is True


def test_the_cloud_in_config_reaches_the_provider(stack_config):
    stack_config(environment="usgovernment")

    assert cluster._provider_settings()["environment"] == "usgovernment", (
        "unset, the SDK sends 'public' and a government cell cannot authenticate"
    )


def test_the_cloud_in_the_environment_is_read_when_config_is_silent(stack_config, monkeypatch):
    stack_config()
    monkeypatch.setenv("ARM_ENVIRONMENT", "usgovernment")

    assert cluster._provider_settings()["environment"] == "usgovernment"


def test_a_subscription_in_config_does_not_outrank_the_one_configured(stack_config):
    stack_config(subscriptionId=THEIRS)

    assert "subscription_id" not in cluster._provider_settings(), "the pin is the whole point"


def test_nothing_is_sent_for_what_the_stack_never_set(stack_config):
    stack_config()

    assert cluster._provider_settings() == {}, "a None would override the provider's own default"


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.title() for word in rest)
