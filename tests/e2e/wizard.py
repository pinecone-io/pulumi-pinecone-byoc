import sys

from .commands import run
from .paths import REPO_ROOT


def parse_wizard_env(wizard_env):
    return dict(
        line.removeprefix("export ").split("=", 1)
        for line in wizard_env.splitlines()
        if line.startswith("export ")
    )


def generate_project(project_dir, stack, cloud, env, skip_install=False):
    project_dir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "setup/wizard.py",
        "--cloud",
        cloud,
        "--headless",
        "--stack-name",
        stack,
        "--output-dir",
        str(project_dir),
    ]
    if skip_install:
        args.append("--skip-install")
    args += ["--dev", str(REPO_ROOT)]
    run(*args, cwd=REPO_ROOT, env=env)
    return project_dir
