import inspect

import pytest

from pulumi_pinecone_byoc.aws.cluster import PineconeAWSCluster
from pulumi_pinecone_byoc.azure.cluster import PineconeAzureCluster
from pulumi_pinecone_byoc.azure.storage import BlobStorage
from pulumi_pinecone_byoc.gcp.cluster import PineconeGCPCluster

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
