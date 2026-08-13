import logging
import os
import threading

from .commands import pulumi
from .installer import capture_failed_deploy, supervise_pinetools_logs
from .paths import PROJECTS
from .settings import keep_stacks
from .stacks import destroy_stack, stack_name
from .wizard import generate_project, non_interactive_env


def deployed_project(request, shape, **answers):
    """Deploy one shape, hand back its project directory, and tear it down after.

    A generator so a fixture can `yield from` it: every shape needs the installer
    log streamer running for the whole deploy and the same --keep handling, and a
    shape that skipped either would be the one nobody could debug.
    """
    stack = stack_name(shape, "byoc")
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        "aws",
        non_interactive_env(request.config, os.environ["AWS_REGION"], stack, **answers),
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
                    "leaving %s stack %s up - destroy it with: cd %s && pulumi destroy --yes",
                    shape,
                    stack,
                    project_dir,
                )
            else:
                destroy_stack(project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)
