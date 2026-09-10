"""Which subscription the cell is built in, decided without deploying one.

The subscription in the stack's config has to be the one every resource lands
in. It used to reach only the ARM ID strings the module builds by hand, while
the resources themselves went to whatever the host's `az account` last pointed
at, so these assert on the provider each resource and each invoke is bound to.

The cluster is built against pulumi's mock engine, so a resource that escapes
the provider fails here rather than only in a cloud run.
"""

import base64

import pulumi
import pytest
from pulumi_azure_native import Provider

from config.azure import AzureConfig
from pulumi_pinecone_byoc.azure import PineconeAzureCluster, PineconeAzureClusterArgs
from pulumi_pinecone_byoc.azure.vnet import VNet

OURS = "11111111-2222-3333-4444-555555555555"
THEIRS = "99999999-8888-7777-6666-555555555555"
CELL = "cell-abcd"
AZURE_PROVIDER = "pulumi:providers:azure-native"
MANAGED_CLUSTER = "azure-native:containerservice:ManagedCluster"
LIST_KEYS = "azure-native:storage:listStorageAccountKeys"
LIST_CREDENTIALS = "azure-native:containerservice:listManagedClusterUserCredentials"


class Engine(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources = []
        self.invokes = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append((args.typ, args.name, args.provider, dict(args.inputs)))
        outputs = dict(args.inputs)
        if args.typ == MANAGED_CLUSTER:
            # the engine echoes inputs back as outputs, and this one is a list
            # going in and a map coming out, so it needs the response shape
            outputs["identity"] = {
                "type": "UserAssigned",
                "userAssignedIdentities": {
                    "/id": {"clientId": "client", "principalId": "principal"}
                },
            }
        # what the control plane would answer, for the dynamic resources
        outputs.setdefault("env_name", "env-abcd.byoc")
        outputs.setdefault("org_id", "org-1")
        outputs.setdefault("org_name", "org")
        outputs.setdefault("name", args.name)
        return f"{args.name}-id", outputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        self.invokes.append((args.token, args.provider))
        if args.token.endswith("getClientConfig"):
            return {"tenantId": "tenant", "subscriptionId": THEIRS, "clientId": "c"}
        if args.token == LIST_KEYS:
            return {"keys": [{"keyName": "key1", "value": "not-a-key"}]}
        if args.token == LIST_CREDENTIALS:
            # no `server:` line, so the kubeconfig waits on no DNS
            return {"kubeconfigs": [{"name": "admin", "value": _b64("apiVersion: v1")}]}
        return {}

    def azure_resources(self):
        return [r for r in self.resources if r[0].startswith("azure-native:")]

    def providers(self):
        """The subscription each azure-native provider was created with."""
        return {
            name: inputs.get("subscriptionId")
            for typ, name, _, inputs in self.resources
            if typ == AZURE_PROVIDER
        }

    def bound_to(self, name):
        """The provider ref a resource created against the provider `name` carries."""
        return f"::{name}::{name}-id"


def _b64(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def engine():
    mocks = Engine()
    pulumi.runtime.set_mocks(mocks, preview=False)
    return mocks


@pytest.fixture
def config():
    return AzureConfig(
        region="eastus2",
        cloud="azure",
        subscription_id=OURS,
        availability_zones=["2", "3"],
    )


def a_cell(subscription_id=OURS, opts=None):
    """Build the cluster and wait for the engine to settle."""

    @pulumi.runtime.test
    def run():
        return PineconeAzureCluster(
            "pinecone-byoc",
            PineconeAzureClusterArgs(
                pinecone_api_key="pcsk_fake",
                pinecone_version="main-0000000",
                subscription_id=subscription_id,
                region="eastus2",
                availability_zones=["2", "3"],
            ),
            opts=opts,
        )

    run()


def test_the_provider_is_pinned_to_the_configured_subscription(engine):
    a_cell()

    assert engine.providers() == {"pinecone-byoc-azure": OURS}


def test_every_azure_resource_in_the_cell_is_created_against_that_provider(engine):
    a_cell()

    created = engine.azure_resources()
    assert len(created) > 1, "the cell builds azure-native resources"

    escaped = [
        (typ, name)
        for typ, name, provider, _ in created
        if not provider.endswith(engine.bound_to("pinecone-byoc-azure"))
    ]
    assert escaped == [], "a resource off that provider lands in the host's subscription"


def test_the_invokes_ask_the_configured_subscription_too(engine):
    a_cell()

    ours = engine.bound_to("pinecone-byoc-azure")
    for token in (LIST_KEYS, LIST_CREDENTIALS):
        asked = [provider for called, provider in engine.invokes if called == token]
        assert asked, f"{token} is what the cell reads its credentials with"
        assert all(provider.endswith(ours) for provider in asked), (
            "an invoke inherits no provider, it has to be passed one"
        )


def test_a_provider_the_caller_brings_is_the_one_that_wins(engine):
    """Ours is merged first, so a caller who supplies their own keeps it."""
    theirs = Provider("theirs", subscription_id=THEIRS)
    a_cell(opts=pulumi.ResourceOptions(providers=[theirs]))

    assert engine.providers() == {"theirs": THEIRS}, "and no second provider beside it"
    assert all(
        provider.endswith(engine.bound_to("theirs"))
        for _, _, provider, _ in engine.azure_resources()
    )


def test_the_invokes_ask_the_provider_the_caller_brought(engine):
    """The credentials a resource is created with are the ones it is read back with."""
    theirs = Provider("theirs", subscription_id=THEIRS)
    a_cell(opts=pulumi.ResourceOptions(providers=[theirs]))

    ref = engine.bound_to("theirs")
    for token in (LIST_KEYS, LIST_CREDENTIALS):
        asked = [provider for called, provider in engine.invokes if called == token]
        assert asked and all(provider.endswith(ref) for provider in asked), (
            f"{token} would ask a subscription the cell was not built in"
        )


def test_without_a_provider_a_resource_carries_no_subscription_of_its_own(engine, config):
    """What the bug looked like: an empty ref is the host's default."""

    @pulumi.runtime.test
    def run():
        return VNet("vnet", config, CELL)

    run()

    assert {provider for _, _, provider, _ in engine.azure_resources()} == {""}
