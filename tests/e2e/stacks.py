import getpass
import json
import os
import re

import boto3

from .commands import pulumi, pulumi_json, pulumi_quiet
from .paths import REPO_ROOT

ARN_ACCOUNT = re.compile(r"arn:aws:[a-z0-9-]*:[a-z0-9-]*:(\d{12}):")


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


def stack_accounts(qualified):
    export = pulumi_json("stack", "export", "--stack", qualified, cwd=REPO_ROOT)
    return set(ARN_ACCOUNT.findall(json.dumps(export)))


def caller_account():
    return boto3.client("sts").get_caller_identity()["Account"]


def refuse_foreign_account(qualified):
    accounts = stack_accounts(qualified)
    if not accounts:
        return

    caller = caller_account()
    if caller not in accounts:
        raise AssertionError(
            f"{qualified} holds resources in {', '.join(sorted(accounts))}, "
            f"but these credentials are for {caller}. Destroying from here would empty "
            f"the state and leave the infrastructure running. "
            f"Set AWS_PROFILE to a profile in {', '.join(sorted(accounts))} - a --profile "
            f"flag does not reach the Pulumi SDK."
        )


def destroy_stack(cwd, stack=None):
    scoped = (["--stack", stack] if stack else []) + ["--yes"]
    pulumi_quiet("cancel", *scoped, cwd=cwd)
    pulumi("destroy", "--yes", "--skip-preview", *(["--stack", stack] if stack else []), cwd=cwd)
    pulumi_quiet("stack", "rm", *([stack] if stack else []), "--yes", cwd=cwd)
