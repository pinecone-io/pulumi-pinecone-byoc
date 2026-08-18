import os

import pytest
from e2e.aws import cluster_from_outputs, parent_zone_id, private_dns_verification_state
from e2e.commands import pulumi, pulumi_json
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


@pytest.fixture
def byodns_parent(request):
    """The domain the cell hangs off, and the zone that already answers for it.

    Named in pytest.ini rather than passed in, because it is a fact about the
    account the shape runs in and not a choice a run makes. Nothing configured is
    a reason to skip locally; CI refuses the run instead, since a label asked for
    the shape and a skip would report a green run that deployed nothing.
    """
    domain = e2e_parent_domain(request.config)
    if not domain:
        pytest.skip("no e2e_parent_domain: this shape needs the BYO-DNS test domain")
    return parent_zone_id(domain), domain


@pytest.fixture
def byodns_project(request, byodns_parent):
    shape = getattr(request, "param", "byodns-public")
    zone_id, domain = byodns_parent
    public_access = "false" if shape.endswith("private") else "true"

    def delegate_from_their_zone(project_dir, stack):
        pulumi("config", "set", "parent-zone-id", zone_id, "--stack", stack, cwd=project_dir)

    for project_dir in deployed_project(
        request,
        shape,
        configure=delegate_from_their_zone,
        PINECONE_DOMAIN=domain,
        PINECONE_PUBLIC_ACCESS=public_access,
    ):
        yield {
            "project_dir": project_dir,
            "domain": domain,
            "public_access": public_access == "true",
        }


@pytest.mark.parametrize("byodns_project", ["byodns-public", "byodns-private"], indirect=True)
def test_e2e_byodns(byodns_project):
    """A cell whose zone Pinecone does not host still answers on it, both ways in.

    Every check resolves the cell's own zone rather than asking the control plane
    where the data plane lives, so the shape holds before a control plane that
    reads a claimed zone has deployed. The PrivateLink name is the strongest of
    them: AWS resolves its ownership record out of public DNS before it will
    attach the name, so a verified one is proof the delegation answers from the
    root - which is the part of BYO-DNS nothing else here can prove.
    """
    project_dir, domain = byodns_project["project_dir"], byodns_project["domain"]
    region = os.environ["AWS_REGION"]

    fqdn = cell_fqdn(project_dir)
    assert fqdn.endswith(f".byoc.{domain}"), f"{fqdn} did not land under the domain we asked for"
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
