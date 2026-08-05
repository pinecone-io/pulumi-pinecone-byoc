import logging
import os

import pytest
from e2e.aws import cluster_from_outputs, grant_cluster_admin
from e2e.commands import pulumi_json, run
from e2e.paths import PROJECTS
from e2e.settings import destroy_targets, e2e_azs
from e2e.stacks import destroy_stack, find_stack, project_of
from e2e.wizard import generate_project

pytestmark = pytest.mark.destroy


def pytest_generate_tests(metafunc):
    if "target" in metafunc.fixturenames:
        targets = destroy_targets(metafunc.config)
        metafunc.parametrize("target", targets, ids=[t.stack for t in targets])


@pytest.fixture
def condemned_project(target, request):
    qualified = find_stack(target.stack)
    if qualified is None:
        pytest.skip(f"no stack named {target.stack} in the organization, nothing to destroy")

    logging.info("found %s, regenerating its project to destroy it", qualified)
    project_dir = generate_project(
        PROJECTS / target.stack,
        target.stack,
        target.cloud,
        {
            "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
            "PINECONE_REGION": target.region,
            "PINECONE_AZS": e2e_azs(request.config),
            "PINECONE_PROJECT_NAME": project_of(qualified),
            "PINECONE_DELETION_PROTECTION": "false",
        },
        skip_install=True,
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
