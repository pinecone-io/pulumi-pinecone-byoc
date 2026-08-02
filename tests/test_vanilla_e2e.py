import logging
import os
import sys
import threading

import pytest
from e2e.commands import pulumi, pulumi_json, run
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS, REPO_ROOT
from e2e.settings import e2e_azs, keep_stacks
from e2e.stacks import destroy_stack, stack_name

pytestmark = pytest.mark.e2e


@pytest.fixture
def vanilla_project(request):
    stack = stack_name("vanilla", "byoc")
    project_dir = PROJECTS / stack
    project_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
        "PINECONE_REGION": os.environ["AWS_REGION"],
        "PINECONE_AZS": e2e_azs(request.config),
        "PINECONE_VPC_CIDR": "10.0.0.0/16",
        "PINECONE_PUBLIC_ACCESS": "true",
        "PINECONE_PROJECT_NAME": stack,
        "PINECONE_DELETION_PROTECTION": "false",
    }

    run(
        sys.executable,
        "setup/wizard.py",
        "--cloud",
        "aws",
        "--headless",
        "--stack-name",
        stack,
        "--output-dir",
        str(project_dir),
        "--dev",
        str(REPO_ROOT),
        cwd=REPO_ROOT,
        env=env,
    )

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs,
        args=(None, stop_streaming),
        daemon=True,
    )
    streamer.start()

    try:
        pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
        yield project_dir
    finally:
        try:
            if keep_stacks(request):
                logging.info(
                    "leaving vanilla stack %s up - destroy it with: cd %s && pulumi destroy --yes",
                    stack,
                    project_dir,
                )
            else:
                destroy_stack(project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


def test_e2e_vanilla(vanilla_project):
    environment = pulumi_json("stack", "output", "--json", cwd=vanilla_project).get("environment")
    assert environment, "the deploy exported no environment"
