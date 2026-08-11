import logging
import os

import pytest
from e2e.aws import cluster_from_outputs, load_balancers_in, vpc_of_cluster
from e2e.commands import pulumi_json
from e2e.deploy import deployed_project
from e2e.kube import ingresses_in, status_from_cluster, write_kubeconfig
from e2e.reachability import assert_never_answers, data_plane_host, private_data_plane_host

pytestmark = pytest.mark.e2e

GLOO = "gloo-system"
PUBLIC_INGRESSES = ("gloo-lb", "gloo-lb-http1")


@pytest.fixture
def private_project(request):
    yield from deployed_project(request, "private", PINECONE_PUBLIC_ACCESS="false")


def test_e2e_private(private_project):
    """A cluster with no way in from the internet still serves over PrivateLink.

    Public mode is checked from the runner, over the internet-facing ALB. There is
    no such ALB here, so the probe runs in the cluster: the endpoint's private DNS
    only resolves inside the VPC, and reaching it proves the whole private path -
    endpoint, NLB, internal ALB, gateway-proxy - not just that the pods are up.
    """
    outputs = pulumi_json("stack", "output", "--json", cwd=private_project)
    environment = outputs.get("environment")
    assert environment, "the deploy exported no environment"
    assert outputs.get("vpc_endpoint_service_name"), (
        "a private deploy exports the endpoint service consumers connect to; "
        "without it there is nothing for them to point a VPC endpoint at"
    )

    cluster = cluster_from_outputs(outputs)
    assert cluster, "the deploy exported no cluster to reach it through"
    region = os.environ["AWS_REGION"]
    kubeconfig = write_kubeconfig(cluster, region)

    ingresses = ingresses_in(kubeconfig, GLOO)
    logging.info("[private] ingresses in %s: %s", GLOO, sorted(ingresses))
    assert not [name for name in PUBLIC_INGRESSES if name in ingresses], (
        f"public access is off, so {GLOO} must carry no internet-facing ingress: "
        f"found {sorted(ingresses)}"
    )
    assert "private-gloo-lb" in ingresses, (
        f"the private ingress is how anything reaches gateway-proxy: found {sorted(ingresses)}"
    )

    balancers = load_balancers_in(vpc_of_cluster(cluster, region), region)
    logging.info("[private] load balancers: %s", balancers)
    assert not [lb for lb in balancers if lb[1] == "internet-facing"], (
        f"nothing in this VPC may face the internet: {balancers}"
    )

    status = status_from_cluster(kubeconfig, f"https://{private_data_plane_host(environment)}/")
    assert status is not None, (
        f"{private_data_plane_host(environment)} never answered from inside the cluster, "
        "so the PrivateLink path is the only way in and it does not work"
    )

    assert_never_answers(data_plane_host(environment))
