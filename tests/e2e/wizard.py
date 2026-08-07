import sys
from pathlib import Path

from .commands import run
from .paths import REPO_ROOT


def parse_wizard_env(wizard_env):
    return dict(
        line.removeprefix("export ").split("=", 1)
        for line in wizard_env.splitlines()
        if line.startswith("export ")
    )


def generate_project(
    project_dir, stack, cloud, env, skip_install=False, source=REPO_ROOT, dev=True
):
    project_dir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        str(Path(source) / "setup" / "wizard.py"),
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
    if dev:
        args += ["--dev", str(source)]
    run(*args, cwd=source, env=env)
    return project_dir
