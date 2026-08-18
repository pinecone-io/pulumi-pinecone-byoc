import logging
import os

import pytest
from e2e.aws import (
    assert_delegated,
    cluster_from_outputs,
    parent_zone,
    private_dns_verification_state,
)
from e2e.commands import pulumi, pulumi_json
from e2e.deploy import deployed_project
from e2e.kube import status_from_cluster, write_kubeconfig
from e2e.paths import REPO_ROOT
from e2e.reachability import (
    assert_answers,
    assert_never_answers,
    cell_fqdn,
    data_plane_host,
    private_data_plane_host,
)
from e2e.settings import e2e_parent_domain, keep_stacks
from e2e.stacks import destroy_stack, stack_name

pytestmark = pytest.mark.e2e

PROGRAM_DIR = REPO_ROOT / "tests" / "dns" / "program"


@pytest.fixture(scope="module")
def their_delegated_zone(request):
    domain = e2e_parent_domain(request.config)
    if not domain:
        pytest.skip("no e2e_parent_domain: this shape needs the BYO-DNS test domain")

    zone_id, nameservers = parent_zone(domain)
    assert_delegated(domain, nameservers)

    stack = stack_name("byodns", "zone")
    pulumi("stack", "select", "--create", stack, cwd=PROGRAM_DIR)
    pulumi(
        "config", "set", "aws:region", os.environ["AWS_REGION"], "--stack", stack, cwd=PROGRAM_DIR
    )
    pulumi("config", "set", "domain", domain, "--stack", stack, cwd=PROGRAM_DIR)
    pulumi("config", "set", "parent-zone-id", zone_id, "--stack", stack, cwd=PROGRAM_DIR)

    try:
        pulumi("up", "--yes", "--skip-preview", "--stack", stack, cwd=PROGRAM_DIR)
        outputs = pulumi_json("stack", "output", "--json", "--stack", stack, cwd=PROGRAM_DIR)
        yield {"zone_id": outputs["zone_id"], "fqdn": outputs["fqdn"], "domain": domain}
    finally:
        if keep_stacks(request):
            logging.info(
                "leaving delegated zone stack %s up - destroy it with: cd %s && "
                "pulumi destroy --yes --stack %s",
                stack,
                PROGRAM_DIR,
                stack,
            )
        else:
            destroy_stack(PROGRAM_DIR, stack)


@pytest.fixture
def byodns_project(request, their_delegated_zone):
    shape = getattr(request, "param", "byodns-public")
    domain = their_delegated_zone["domain"]
    public_access = "false" if shape.endswith("private") else "true"

    def delegate_from_the_zone_we_host(project_dir, stack):
        pulumi(
            "config",
            "set",
            "parent-zone-id",
            their_delegated_zone["zone_id"],
            "--stack",
            stack,
            cwd=project_dir,
        )

    for project_dir in deployed_project(
        request,
        shape.removesuffix("-public"),
        configure=delegate_from_the_zone_we_host,
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
