import contextlib
import logging
import os

import pytest
from e2e.aws import (
    assert_can_delegate,
    assert_delegated,
    cell_zone,
    cluster_from_outputs,
    delegate,
    parent_zone,
    private_dns_verification_state,
    sweep_stale_delegations,
)
from e2e.commands import pulumi_json
from e2e.deploy import deployed_project
from e2e.kube import status_from_cluster, write_kubeconfig
from e2e.reachability import (
    assert_answers,
    assert_never_answers,
    cell_fqdn,
    data_plane_host,
    private_data_plane_host,
)
from e2e.settings import e2e_parent_domain

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def their_delegated_zone(request):
    domain = e2e_parent_domain(request.config)
    if not domain:
        pytest.skip("no e2e_parent_domain: this shape needs the BYO-DNS test domain")

    zone_id, nameservers = parent_zone(domain)
    assert_delegated(domain, nameservers)
    assert_can_delegate(zone_id, domain)
    sweep_stale_delegations(zone_id, domain)
    return {"zone_id": zone_id, "domain": domain}


@pytest.fixture
def byodns_project(request, their_delegated_zone):
    shape = getattr(request, "param", "byodns-public")
    domain = their_delegated_zone["domain"]
    public_access = "false" if shape.endswith("private") else "true"

    delegated = []

    def as_the_customer_would(project_dir):
        fqdn, nameservers = cell_zone(pulumi_json("stack", "export", cwd=project_dir))
        logging.info("[byodns] delegating %s to %s", fqdn, nameservers)
        delegate(their_delegated_zone["zone_id"], fqdn, nameservers)
        delegated.append((fqdn, nameservers))

    def undelegate():
        for fqdn, nameservers in delegated:
            with contextlib.suppress(Exception):
                delegate(their_delegated_zone["zone_id"], fqdn, nameservers, action="DELETE")

    try:
        for project_dir in deployed_project(
            request,
            shape.removesuffix("-public"),
            delegate=as_the_customer_would,
            PINECONE_DOMAIN=domain,
            PINECONE_PUBLIC_ACCESS=public_access,
        ):
            yield {
                "project_dir": project_dir,
                "domain": domain,
                "public_access": public_access == "true",
            }
    finally:
        undelegate()


@pytest.mark.parametrize("byodns_project", ["byodns-public", "byodns-private"], indirect=True)
def test_e2e_byodns(byodns_project):
    project_dir, domain = byodns_project["project_dir"], byodns_project["domain"]
    region = os.environ["AWS_REGION"]

    fqdn = cell_fqdn(project_dir)
    assert fqdn.endswith(f".{domain}"), f"{fqdn} did not land under the domain we asked for"
    assert ".byoc." not in fqdn, f"{fqdn} carries our own label inside a domain the customer owns"
    assert private_dns_verification_state(fqdn, region) == "verified"

    if byodns_project["public_access"]:
        assert_answers(data_plane_host(fqdn))
        return

    cluster = cluster_from_outputs(pulumi_json("stack", "output", "--json", cwd=project_dir))
    assert cluster, "the deploy exported no cluster to reach it through"
    kubeconfig = write_kubeconfig(cluster, region)

    status = status_from_cluster(kubeconfig, f"https://{private_data_plane_host(fqdn)}/")
    assert status == 200, (
        f"{private_data_plane_host(fqdn)} answered {status} from inside the cluster, "
        "so the PrivateLink path is the only way in and it does not serve the cell"
    )

    assert_never_answers(data_plane_host(fqdn))
