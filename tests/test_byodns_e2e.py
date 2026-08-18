import logging
import os
import threading

import pytest
from e2e.aws import parent_zone_name, private_dns_verification_state
from e2e.commands import pulumi
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS
from e2e.reachability import assert_answers, cell_fqdn, data_plane_host
from e2e.settings import keep_stacks
from e2e.stacks import destroy_stack, stack_name
from e2e.wizard import generate_project, non_interactive_env

pytestmark = pytest.mark.e2e


@pytest.fixture
def byodns_project(request):
    zone_id = os.environ.get("PINECONE_PARENT_ZONE_ID")
    if not zone_id:
        pytest.skip("no PINECONE_PARENT_ZONE_ID: this shape needs the BYO-DNS test domain")
    domain = parent_zone_name(zone_id)

    stack = stack_name("byodns", "byoc")
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        "aws",
        non_interactive_env(
            request.config,
            os.environ["AWS_REGION"],
            stack,
            PINECONE_DOMAIN=domain,
        ),
    )
    pulumi("config", "set", "parent-zone-id", zone_id, "--stack", stack, cwd=project_dir)

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs,
        args=(None, stop_streaming),
        daemon=True,
    )
    streamer.start()

    try:
        pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
        yield project_dir, domain
    finally:
        try:
            if keep_stacks(request):
                logging.info(
                    "leaving byodns stack %s up - destroy it with: cd %s && pulumi destroy --yes",
                    stack,
                    project_dir,
                )
            else:
                destroy_stack(project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


def test_e2e_byodns(byodns_project):
    """A cell whose zone Pinecone does not host still answers on it.

    The check resolves the cell's own zone rather than asking the control plane where
    the data plane lives, so it holds before a control plane that reads a claimed zone
    has deployed.
    """
    project_dir, domain = byodns_project
    fqdn = cell_fqdn(project_dir)
    assert fqdn.endswith(f".byoc.{domain}"), f"{fqdn} did not land under the domain we asked for"
    assert_answers(data_plane_host(fqdn))
    assert private_dns_verification_state(fqdn, os.environ["AWS_REGION"]) == "verified"
