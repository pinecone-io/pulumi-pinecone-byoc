import logging
from pathlib import Path

import pytest
from e2e.aws import cluster_from_outputs, grant_cluster_admin
from e2e.commands import pulumi_json, run
from e2e.paths import PROJECTS
from e2e.settings import destroy_targets
from e2e.stacks import (
    destroy_stack,
    find_stack,
    project_of,
    refuse_foreign_account,
    stack_name,
)
from e2e.wizard import generate_project, non_interactive_env

pytestmark = pytest.mark.destroy

VPC_PROGRAM_DIR = Path(__file__).resolve().parent / "vpc" / "program"
E2E_STANDIN = ("byovpc", "vpc")
E2E_INSIDE_IT = (("byovpc", "byoc"), ("byovpc-private", "byoc"))


def pytest_generate_tests(metafunc):
    if "target" in metafunc.fixturenames:
        targets = destroy_targets(metafunc.config)
        metafunc.parametrize("target", targets, ids=[t.stack for t in targets])


@pytest.fixture
def condemned_project(target, request):
    qualified = find_stack(target.stack)
    if qualified is None:
        pytest.skip(f"no stack named {target.stack} in the organization, nothing to destroy")

    if target.cloud == "aws":
        refuse_foreign_account(qualified)

    logging.info("found %s, regenerating its project to destroy it", qualified)
    project_dir = generate_project(
        PROJECTS / target.stack,
        target.stack,
        target.cloud,
        non_interactive_env(request.config, target.region, project_of(qualified)),
        skip_install=True,
        destroy=True,
    )
    run("uv", "sync", cwd=project_dir)

    if request.config.getoption("--grant-cluster-access"):
        _grant_cluster_access(project_dir, target)

    return project_dir


def _grant_cluster_access(project_dir, target):
    if target.cloud != "aws":
        logging.info("--grant-cluster-access only applies to aws, skipping for %s", target.cloud)
        return
    try:
        outputs = pulumi_json("stack", "output", "--json", "--stack", target.stack, cwd=project_dir)
    except AssertionError as exit_status:
        logging.info("no stack outputs to read a cluster from, not granting: %s", exit_status)
        return
    cluster = cluster_from_outputs(outputs)
    if cluster is None:
        logging.info("%s exports no cluster, nothing to grant", target.stack)
        return
    principal = grant_cluster_admin(cluster, target.region)
    logging.info("[eks] granted %s cluster-admin on %s", principal, cluster)


def test_e2e_destroy(condemned_project, target):
    destroy_stack(condemned_project, target.stack)
    assert find_stack(target.stack) is None, (
        f"{target.stack} is still in the organization after destroy"
    )


def test_the_e2e_leaves_no_stand_in_vpc_behind():
    """After the clusters above, whose subnets are inside this VPC.

    Same file and below them, because that is the only thing that orders two
    tests - the destroy would fail on subnets it does not own, and leave the
    network up.
    """
    for shape in E2E_INSIDE_IT:
        inside = stack_name(*shape)
        assert find_stack(inside) is None, (
            f"{inside} still holds subnets in the stand-in VPC - it has to go first"
        )

    stack = stack_name(*E2E_STANDIN)
    if find_stack(stack) is None:
        pytest.skip(f"no stack named {stack} in the organization, nothing to destroy")

    logging.info("destroying %s left behind in %s", stack, VPC_PROGRAM_DIR)
    destroy_stack(VPC_PROGRAM_DIR, stack)
