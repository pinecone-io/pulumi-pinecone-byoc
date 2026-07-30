"""End-to-end runs: provision a stand-in customer VPC, generate a BYOC project
against it with the headless wizard, and deploy it.

There are deliberately no assertions beyond the deploy succeeding -- `pulumi up`
returning non-zero is the failure signal. Deselected by default; each run takes
tens of minutes and provisions real infrastructure.

    pytest -m e2e tests/test_byovpc_e2e.py -k e2e_carve   -s

Requires PINECONE_API_KEY. Pass --keep-vpc to leave both stacks up.

Runs take tens of minutes, so prefix with `caffeinate -i` on macOS. If a run is
interrupted, the generated project stays in .e2e/<stack>/ and teardown is:

    cd .e2e/$USER-<mode>-byoc && pulumi destroy --yes
    cd tests/fixtures/byovpc && pulumi destroy --yes --stack $USER-<mode>
"""

import logging
import os
import sys
import threading

import pytest
from e2e.commands import pulumi, run
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS, REPO_ROOT
from e2e.settings import keep_stacks
from e2e.stacks import destroy_stack, stack_name
from e2e.wizard import parse_wizard_env

pytestmark = pytest.mark.e2e


@pytest.fixture
def byoc_project(request, byovpc):
    api_key = os.environ["PINECONE_API_KEY"]
    mode = byovpc["mode"]
    stack = stack_name(mode, "byoc")
    project_dir = PROJECTS / stack
    project_dir.mkdir(parents=True, exist_ok=True)

    env = parse_wizard_env(byovpc["wizard_env"])
    env["PINECONE_API_KEY"] = api_key
    env["PINECONE_PROJECT_NAME"] = stack
    env["PINECONE_DELETION_PROTECTION"] = "false"

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
        args=(byovpc["vpc_id"], stop_streaming),
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
                    f"leaving BYOC stack {stack} up - destroy it with: "
                    f"cd {project_dir} && caffeinate -i pulumi destroy --yes"
                )
                print(f"\n{message}")
                logging.info(message)
            else:
                destroy_stack(project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


@pytest.mark.parametrize("byovpc", ["carve"], indirect=True)
def test_e2e_carve(byoc_project):
    print(f"\ndeployed BYOC with module-carved subnets: {byoc_project}")
