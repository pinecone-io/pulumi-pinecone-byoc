import importlib.util
import inspect

import pytest

# A cross-cloud assertion needs every provider SDK. The aws e2e jobs install only
# the aws extra and still collect this file, so skip rather than fail collection;
# ci.yaml runs with --all-extras and is where these actually assert.
pytest.importorskip("pulumi_aws")
pytest.importorskip("pulumi_azure_native")
pytest.importorskip("pulumi_gcp")

from wizard import ZONES_OFFERED, AWSSetupWizard, AzureSetupWizard, GCPSetupWizard  # noqa: E402

from pulumi_pinecone_byoc.aws.cluster import PineconeAWSClusterArgs  # noqa: E402
from pulumi_pinecone_byoc.azure.cluster import PineconeAzureClusterArgs  # noqa: E402
from pulumi_pinecone_byoc.common.k8s_configmaps import (  # noqa: E402
    DATA_PLANE_BACKEND,
    K8sConfigMaps,
)
from pulumi_pinecone_byoc.gcp.cluster import PineconeGCPClusterArgs  # noqa: E402

DATABASE_MODULES = [
    "pulumi_pinecone_byoc.aws.rds",
    "pulumi_pinecone_byoc.azure.database",
    "pulumi_pinecone_byoc.gcp.alloydb",
]


def test_the_only_backend_is_fdb():
    assert DATA_PLANE_BACKEND == "fdb"


@pytest.mark.parametrize("module", DATABASE_MODULES)
def test_no_cloud_ships_a_database_component(module):
    assert importlib.util.find_spec(module) is None


def test_both_configmaps_carry_the_backend_from_the_same_constant():
    source = inspect.getsource(K8sConfigMaps.__init__)

    assert source.count('"data_plane_backend": DATA_PLANE_BACKEND') == 2


def test_no_caller_can_name_a_backend():
    assert "data_plane_backend" not in inspect.signature(K8sConfigMaps.__init__).parameters


@pytest.mark.parametrize(
    "args_class",
    [PineconeAWSClusterArgs, PineconeAzureClusterArgs, PineconeGCPClusterArgs],
)
def test_every_cloud_offers_the_same_zone_count(args_class):
    default = args_class.__dataclass_fields__["availability_zones"].default_factory()

    assert len(default) == ZONES_OFFERED


@pytest.mark.parametrize("wizard_class", [AWSSetupWizard, AzureSetupWizard, GCPSetupWizard])
def test_fewer_zones_deploy_but_say_so(wizard_class, monkeypatch, capsys):
    wizard = wizard_class.__new__(wizard_class)
    wizard._state = None
    wizard._non_interactive = True
    monkeypatch.setattr(wizard_class, "_prompt", lambda *a, **k: "us-east-2a,us-east-2b")

    zones = wizard._prompt_zones("Enter AZs", ["us-east-2a", "us-east-2b", "us-east-2c"])

    assert zones == ["us-east-2a", "us-east-2b"]
    assert "replica" in capsys.readouterr().out
