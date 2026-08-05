import getpass
import os

from .commands import pulumi, pulumi_json, pulumi_quiet
from .paths import REPO_ROOT


def stack_name(*parts):
    prefix = os.environ.get("E2E_STACK_PREFIX") or os.environ.get("USER") or getpass.getuser()
    return "-".join([prefix, *parts])


def find_stack(name):
    """Locate a stack anywhere in the organization, as org/project/stack.

    A bare `pulumi stack ls` only sees the project rooted at the working
    directory, so it misses a stack whose project name is not the one the
    caller happens to be standing in - and it errors outright when there is no
    project there at all. `--all` asks the backend instead of the filesystem.
    """
    for stack in pulumi_json("stack", "ls", "--all", "--json", cwd=REPO_ROOT):
        if stack["name"].rsplit("/", 1)[-1] == name:
            return stack["name"]
    return None


def project_of(qualified):
    return qualified.split("/")[-2]


def destroy_stack(cwd, stack=None):
    scoped = (["--stack", stack] if stack else []) + ["--yes"]
    pulumi_quiet("cancel", *scoped, cwd=cwd)
    pulumi("destroy", "--yes", "--skip-preview", *(["--stack", stack] if stack else []), cwd=cwd)
    pulumi_quiet("stack", "rm", *([stack] if stack else []), "--yes", cwd=cwd)
