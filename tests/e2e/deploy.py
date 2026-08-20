import contextlib
import logging
import threading

from .commands import pulumi
from .installer import capture_failed_deploy, supervise_pinetools_logs
from .paths import PROJECTS
from .settings import e2e_region, keep_stacks
from .stacks import destroy_stack, stack_name
from .wizard import generate_project, non_interactive_env


@contextlib.contextmanager
def _pinetools_logs(cloud):
    if cloud != "aws":
        yield
        return
    stop = threading.Event()
    streamer = threading.Thread(target=supervise_pinetools_logs, args=(None, stop), daemon=True)
    streamer.start()
    try:
        yield
    finally:
        stop.set()
        streamer.join(timeout=10)


def deployed_project(request, shape, cloud="aws", **answers):
    stack = stack_name(shape, "byoc")
    region = e2e_region(request.config, cloud)
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        cloud,
        non_interactive_env(request.config, region, stack, cloud=cloud, **answers),
    )

    with _pinetools_logs(cloud):
        try:
            try:
                pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
            except BaseException:
                capture_failed_deploy(project_dir, stack)
                raise
            yield project_dir
        finally:
            if keep_stacks(request):
                logging.info(
                    "leaving %s stack %s up - destroy it with: cd %s && pulumi destroy --yes",
                    shape,
                    stack,
                    project_dir,
                )
            else:
                destroy_stack(project_dir)
