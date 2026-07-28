"""Control run: deploy BYOC the ordinary way, with the module creating its own VPC.

This exists to separate module-wide problems from BYO-VPC ones. The installer job
is killed at its 1800s deadline in adopt/carve runs; if the same happens here,
with no adopted network involved, the deadline is simply too short for a cold
install rather than anything to do with bringing your own VPC.

Like the BYO-VPC e2e tests there are no assertions - a non-zero `pulumi up` is
the failure signal.

    pytest -m e2e tests/test_vanilla_e2e.py -k e2e_vanilla -s

Requires PINECONE_API_KEY. Interrupted runs leave the project in
.e2e/<stack>/ so teardown is `cd .e2e/$USER-vanilla-byoc && pulumi destroy --yes`.
"""

import os
import sys
import threading

import pytest
from byovpc_util import (
    REPO_ROOT,
    delete_project_indexes,
    log_line,
    pulumi,
    run,
    stack_name,
    supervise_pinetools_logs,
)
from conftest import keep_stacks

pytestmark = pytest.mark.e2e


@pytest.fixture
def vanilla_project(request):
    """Generate and deploy a BYOC project with no existing VPC, then destroy it."""
    stack = stack_name("vanilla", "byoc")
    project_dir = REPO_ROOT / ".e2e" / stack
    project_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
        "PINECONE_REGION": os.environ["AWS_REGION"],
        "PINECONE_AZS": request.config.getini("byovpc_azs"),
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

    # the module creates the VPC here, so the cluster is identified as the one
    # that did not exist when this run started
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
                message = (
                    f"leaving vanilla stack {stack} up - destroy it with: "
                    f"cd {project_dir} && pulumi destroy --yes"
                )
                print(f"\n{message}")
                log_line(message)
            else:
                delete_project_indexes(project_dir)
                pulumi("destroy", "--yes", "--skip-preview", cwd=project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


def test_e2e_vanilla(vanilla_project):
    print(f"\ndeployed BYOC with a module-created VPC: {vanilla_project}")
