import logging
import os
import threading

import pytest
from e2e.commands import pulumi, pulumi_json
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS
from e2e.reachability import assert_answers, data_plane_host
from e2e.settings import keep_stacks
from e2e.stacks import DEFAULT_SHAPE, STACK_SUFFIX, destroy_stack, stack_name
from e2e.wizard import generate_project, non_interactive_env

pytestmark = [pytest.mark.cloud, pytest.mark.e2e]


@pytest.fixture
def vanilla_project(request):
    stack = stack_name(DEFAULT_SHAPE, STACK_SUFFIX)
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
        pulumi("up", "--yes", "--skip-preview", "--stack", stack, cwd=project_dir)
        yield project_dir, stack
    finally:
        try:
            if keep_stacks(request):
                logging.info(
                    "leaving vanilla stack %s up - destroy it with: "
                    "pulumi destroy --yes -C %s --stack %s",
                    stack,
                    project_dir,
                    stack,
                )
            else:
                destroy_stack(project_dir, stack)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


def test_e2e_vanilla(vanilla_project):
    project_dir, stack = vanilla_project
    environment = pulumi_json("stack", "output", "--json", "--stack", stack, cwd=project_dir).get(
        "environment"
    )
    assert environment, "the deploy exported no environment"
    assert_answers(data_plane_host(environment))
