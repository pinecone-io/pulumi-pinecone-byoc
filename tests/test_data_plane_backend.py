"""Which data plane a cell gets, and the two places that have to agree on it.

pinecone-db decides everything from one configmap key: `data_plane_backend: fdb`
turns on the FoundationDB releases, pins every store to them, stops mounting RDS
credentials, and makes pinetools skip the Postgres migration jobs. A module that
builds RDS anyway is paying for a database nothing reads.
"""

import inspect

import pytest
from wizard import DATA_PLANE_BACKEND, ZONES_NEEDED, AWSSetupWizard

from pulumi_pinecone_byoc.aws.cluster import PineconeAWSCluster, PineconeAWSClusterArgs
from pulumi_pinecone_byoc.common.k8s_configmaps import K8sConfigMaps

THREE_AZS = ["us-east-2a", "us-east-2b", "us-east-2c"]


def test_the_wizard_writes_a_backend_the_program_understands():
    assert DATA_PLANE_BACKEND in ("postgres", "fdb")


def test_a_default_cell_runs_on_fdb_and_builds_no_database():
    assert PineconeAWSClusterArgs.data_plane_backend == "fdb"
    assert DATA_PLANE_BACKEND == "fdb"


def test_the_defaults_can_actually_deploy_together():
    """fdb needs three zones for zone fault domains, so a two-zone default would
    refuse its own backend."""
    args = PineconeAWSClusterArgs(
        pinecone_api_key="pcsk_not_a_key", pinecone_version="main-0000000"
    )

    assert len(args.availability_zones) >= 3
    assert ZONES_NEEDED == 3


def test_the_wizard_and_the_program_agree_on_the_key():
    """The program defaults to postgres, so a key the wizard never writes is a
    silent RDS, and a key spelled differently at either end is the same thing."""
    source = inspect.getsource(AWSSetupWizard._generate_project)

    assert "data-plane-backend: {DATA_PLANE_BACKEND}" in source
    assert 'data_plane_backend=config.get("data-plane-backend")' in source


def test_the_cell_information_configmap_carries_the_backend():
    """The one key pinecone-db reads to turn FoundationDB on and Postgres off."""
    source = inspect.getsource(K8sConfigMaps.__init__)

    assert '"data_plane_backend": data_plane_backend' in source


def test_every_cloud_has_to_name_its_backend():
    """Defaulting the configmap key would give a cloud that forgot it the Postgres
    stores without a Postgres to put them in."""
    backend = inspect.signature(K8sConfigMaps.__init__).parameters["data_plane_backend"]

    assert backend.default is inspect.Parameter.empty


def test_an_fdb_cell_builds_no_database():
    source = inspect.getsource(PineconeAWSCluster.__init__)
    conditional = source.split("self._rds = ", 1)[1].split("self._k8s_addons", 1)[0]

    assert 'if args.data_plane_backend == "postgres"' in conditional
    assert "else None" in conditional


def test_fdb_needs_three_zones_to_keep_quorum():
    args = PineconeAWSClusterArgs(
        pinecone_api_key="pcsk_not_a_key",
        pinecone_version="main-0000000",
        availability_zones=["us-east-2a", "us-east-2b"],
        data_plane_backend="fdb",
    )

    with pytest.raises(ValueError, match="at least 3 availability zones"):
        PineconeAWSCluster("cell", args)


def test_an_unknown_backend_is_refused_before_anything_is_built():
    args = PineconeAWSClusterArgs(
        pinecone_api_key="pcsk_not_a_key",
        pinecone_version="main-0000000",
        availability_zones=THREE_AZS,
        data_plane_backend="mysql",
    )

    with pytest.raises(ValueError, match="must be 'postgres' or 'fdb'"):
        PineconeAWSCluster("cell", args)


def test_both_configmaps_carry_the_backend():
    """pinecone-db reads it twice, from two different places, and they do different
    things: pc-cluster-information drives pinetools (skip the Postgres jobs), and
    the pulumi-outputs JSON becomes helmfile's .Values.config (install FoundationDB,
    stop mounting exdb credentials). PR #86 shipped only the first, and every
    workload came up asking for a secret no RDS had created."""
    source = inspect.getsource(PineconeAWSCluster.__init__)
    outputs = source.split("pulumi_outputs = {", 1)[1].split("}", 1)[0]

    assert '"data_plane_backend": args.data_plane_backend' in outputs
    assert "data_plane_backend=args.data_plane_backend," in source
