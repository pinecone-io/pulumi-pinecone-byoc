import getpass
import os

from .commands import pulumi, pulumi_quiet


def stack_name(*parts):
    prefix = os.environ.get("E2E_STACK_PREFIX") or os.environ.get("USER") or getpass.getuser()
    return "-".join([prefix, *parts])


def destroy_stack(cwd, stack=None):
    scoped = (["--stack", stack] if stack else []) + ["--yes"]
    pulumi_quiet("cancel", *scoped, cwd=cwd)
    pulumi("destroy", "--yes", "--skip-preview", *(["--stack", stack] if stack else []), cwd=cwd)
    pulumi_quiet("stack", "rm", *(["--stack", stack] if stack else []), "--yes", "--force", cwd=cwd)
