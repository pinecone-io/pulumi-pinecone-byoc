import getpass
import json
import logging
import os
import re
import time

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


NODEGROUP_STILL_DELETING = "ResourceInUseException"
DESTROY_ATTEMPTS = 8
DESTROY_RETRY_SECONDS = 90


EKS_CLUSTER_TYPE = "aws:eks/cluster:Cluster"


def eks_cluster_in_state(cwd, stack):
    """The EKS cluster name recorded in the stack's state.

    Outputs are what a stack exports after a *successful* update; a stack whose
    `pulumi up` failed has none, but its state still names every resource it made.
    """
    export = pulumi_json("stack", "export", "--stack", stack, cwd=cwd)
    for resource in export.get("deployment", {}).get("resources", []):
        if resource.get("type") == EKS_CLUSTER_TYPE:
            return (resource.get("outputs") or {}).get("name") or (
                resource.get("inputs") or {}
            ).get("name")
    return None


def destroy_stack(cwd, stack=None):
    scoped = (["--stack", stack] if stack else []) + ["--yes"]
    pulumi_quiet("cancel", *scoped, cwd=cwd)
    for attempt in range(1, DESTROY_ATTEMPTS + 1):
        try:
            pulumi(
                "destroy",
                "--yes",
                "--skip-preview",
                *(["--stack", stack] if stack else []),
                cwd=cwd,
            )
            break
        except AssertionError as failure:
            # the uninstall Job deletes the operator's executor nodegroups asynchronously;
            # EKS refuses to delete the cluster (409) until they are gone.
            if NODEGROUP_STILL_DELETING not in str(failure) or attempt == DESTROY_ATTEMPTS:
                raise
            logging.info(
                "[destroy] EKS still has nodegroups attached, retrying in %ss (%s/%s)",
                DESTROY_RETRY_SECONDS,
                attempt,
                DESTROY_ATTEMPTS,
            )
            time.sleep(DESTROY_RETRY_SECONDS)
    pulumi_quiet("stack", "rm", *([stack] if stack else []), "--yes", cwd=cwd)
