import logging
import os
import threading

import pytest
from e2e.commands import pulumi, pulumi_json
from e2e.installer import capture_failed_deploy, supervise_pinetools_logs
from e2e.paths import PROJECTS
from e2e.reachability import assert_answers, data_plane_host
from e2e.settings import keep_stacks
from e2e.stacks import destroy_stack, stack_name
from e2e.wizard import generate_project, non_interactive_env

pytestmark = pytest.mark.e2e


@pytest.fixture
def vanilla_project(request):
    stack = stack_name("vanilla", "byoc")
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        "aws",
        non_interactive_env(request.config, os.environ["AWS_REGION"], stack),
    )

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs,
        args=(None, stop_streaming),
        daemon=True,
    )
    streamer.start()

    try:
        try:
            pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
        except BaseException:
            capture_failed_deploy(os.environ["AWS_REGION"])
            raise
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
    assert_answers(data_plane_host(environment))
