import logging
import os
import threading

from .commands import pulumi
from .installer import capture_failed_deploy, supervise_pinetools_logs
from .paths import PROJECTS
from .settings import keep_stacks
from .stacks import destroy_stack, stack_name
from .wizard import generate_project, non_interactive_env


def deploy(project_dir, delegate=None):
    """Up, and for a shape whose zone somebody else has to delegate, up again.

    The first one stops at the zone with the records nobody has added yet, which is
    what a customer sees. delegate adds them the way a customer would, in a zone the
    module has no credential for, and the second one carries on from there.
    """
    if delegate is None:
        pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
        return

    try:
        pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
    except Exception as stopped:
        logging.info("[byodns] the first deploy stopped, as a customer's would: %s", stopped)
        try:
            delegate(project_dir)
        except Exception as before_the_zone:
            # it did not get as far as the zone, so it stopped for something else
            raise stopped from before_the_zone
    else:
        raise AssertionError(
            "the deploy did not stop for a delegation, so nothing here exercised one - "
            "an earlier run may have left an NS record for this cell in the parent zone"
        )
    pulumi("up", "--yes", "--skip-preview", cwd=project_dir)


def deployed_project(request, shape, configure=None, delegate=None, **answers):
    stack = stack_name(shape, "byoc")
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        "aws",
        non_interactive_env(request.config, os.environ["AWS_REGION"], stack, **answers),
    )

    if configure is not None:
        configure(project_dir, stack)

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs,
        args=(None, stop_streaming),
        daemon=True,
    )
    streamer.start()

    try:
        try:
            deploy(project_dir, delegate)
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
