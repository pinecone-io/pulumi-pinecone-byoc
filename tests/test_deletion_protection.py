import inspect

import pytest

# A cross-cloud assertion needs every provider SDK. The aws e2e jobs install only
# the aws extra and still collect this file, so skip rather than fail collection;
# ci.yaml runs with --all-extras and is where these actually assert.
pytest.importorskip("pulumi_aws")
pytest.importorskip("pulumi_azure_native")
pytest.importorskip("pulumi_gcp")

from pulumi_pinecone_byoc.aws.cluster import PineconeAWSCluster  # noqa: E402
from pulumi_pinecone_byoc.azure.cluster import PineconeAzureCluster  # noqa: E402
from pulumi_pinecone_byoc.azure.storage import BlobStorage  # noqa: E402
from pulumi_pinecone_byoc.gcp.cluster import PineconeGCPCluster  # noqa: E402

CLUSTERS = [PineconeAWSCluster, PineconeAzureCluster, PineconeGCPCluster]


@pytest.mark.parametrize("cluster_class", CLUSTERS)
def test_every_cloud_still_consumes_deletion_protection(cluster_class):
    source = inspect.getsource(cluster_class.__init__)

    assert "args.deletion_protection" in source


def test_blob_storage_takes_the_flag():
    assert "deletion_protection" in inspect.signature(BlobStorage.__init__).parameters


def test_blob_storage_protects_the_data_it_holds():
    source = inspect.getsource(BlobStorage.__init__)

    assert "protect=deletion_protection" in source
    assert source.count("opts=data_opts") == 2
