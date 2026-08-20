import os
import sys

from .commands import run
from .paths import REPO_ROOT
from .settings import e2e_azs


def non_interactive_env(config, region, project_name, cloud="aws", **overrides):
    """The answers a non-interactive run needs; AWS refuses a run that names no CIDR."""
    return {
        "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
        "PINECONE_REGION": region,
        "PINECONE_AZS": e2e_azs(config, cloud),
        "PINECONE_VPC_CIDR": "default",
        "PINECONE_PUBLIC_ACCESS": "true",
        "PINECONE_PROJECT_NAME": project_name,
        "PINECONE_DELETION_PROTECTION": "false",
    } | overrides


def parse_wizard_env(wizard_env):
    return dict(
        line.removeprefix("export ").split("=", 1)
        for line in wizard_env.splitlines()
        if line.startswith("export ")
    )


def generate_project(project_dir, stack, cloud, env, skip_install=False, destroy=False):
    project_dir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "setup/wizard.py",
        "--cloud",
        cloud,
        "--non-interactive",
        "--stack-name",
        stack,
        "--output-dir",
        str(project_dir),
    ]
    if skip_install:
        args.append("--skip-install")
    if destroy:
        args.append("--destroy")
    args += ["--dev", str(REPO_ROOT)]
    run(*args, cwd=REPO_ROOT, env={"PULUMI_BACKEND": "cloud", **env})
    return project_dir
