"""Shared pytest setup: make the setup/ wizard importable as `wizard`."""

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))

from byovpc_util import (  # noqa: E402
    FIXTURE_DIR,
    REPO_ROOT,
    log_line,
    log_path,
    pulumi,
    pulumi_json,
    set_log_path,
    stack_name,
)


def pytest_addoption(parser):
    parser.addini("aws_profile", "AWS profile used by integration tests", default="byoc-dev")
    parser.addini("aws_region", "AWS region used by integration tests", default="us-east-2")
    parser.addini(
        "byovpc_azs",
        "comma-separated AZs for the byovpc fixture",
        default="us-east-2a,us-east-2b",
    )
    parser.addoption(
        "--keep-vpc",
        action="store_true",
        default=False,
        help="leave provisioned stacks up after integration/e2e tests",
    )
    parser.addoption(
        "--keep-failed",
        action="store_true",
        default=False,
        help="leave provisioned stacks up only when the test fails, so it can be inspected",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report


def keep_stacks(request):
    """True when the stack should survive teardown: always with --keep-vpc, or
    with --keep-failed when this test errored."""
    if request.config.getoption("--keep-vpc"):
        return True
    if not request.config.getoption("--keep-failed"):
        return False
    return any(
        getattr(getattr(request.node, f"rep_{phase}", None), "failed", False)
        for phase in ("setup", "call")
    )


def pytest_configure(config):
    if not os.environ.get("BYOVPC_LOG"):
        selection = (config.option.keyword or config.option.markexpr or "all").replace(" ", "")
        selection = re.sub(r"[^A-Za-z0-9_-]", "", selection)[:40] or "all"
        started = datetime.now().strftime("%Y%m%d-%H%M%S")
        set_log_path(REPO_ROOT / ".byovpc-logs" / f"{started}-{selection}-{os.getpid()}.log")

    os.environ.setdefault("AWS_PROFILE", config.getini("aws_profile"))
    region = config.getini("aws_region")
    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_DEFAULT_REGION", region)
    log_line()
    log_line(
        f"=== session start: -m {config.option.markexpr!r} -k {config.option.keyword!r} "
        f"profile={os.environ['AWS_PROFILE']} region={region}"
    )


def _lock_path():
    return REPO_ROOT / ".e2e" / "run.lock"


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def acquire_run_lock():
    """Prevent two provisioning runs at once.

    Concurrent runs share one AWS account and one Pinecone org, and the vanilla
    test identifies its cluster as "the one that appeared since I started", so
    overlapping runs produce results that cannot be trusted.
    """
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{os.getpid()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = path.read_text().strip()
        pid = int(existing.split()[0]) if existing.split()[:1] else 0
        if pid and _pid_alive(pid):
            raise pytest.UsageError(
                f"another provisioning run is in progress ({existing}); "
                f"wait for it to finish or remove {path} if it is stale"
            ) from None
        log_line(f"taking over stale lock from {existing or 'unknown'}")
        path.write_text(payload)
        return path
    with os.fdopen(handle, "w") as fh:
        fh.write(payload)
    return path


def release_run_lock():
    path = _lock_path()
    try:
        if path.exists() and path.read_text().split()[:1] == [str(os.getpid())]:
            path.unlink()
    except OSError:
        pass


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """trylast so marker/keyword deselection has already pruned `items`; otherwise
    the e2e guard below would fire on runs that never selected an e2e test."""
    log_line(f"selected {len(items)} test(s): {[i.name for i in items]}")
    needs_api_key = [i for i in items if i.get_closest_marker("e2e")]
    if needs_api_key and not os.environ.get("PINECONE_API_KEY"):
        message = (
            "PINECONE_API_KEY must be set to run e2e tests; nothing was provisioned. "
            "Export it and re-run."
        )
        log_line(f"ABORT {message}")
        raise pytest.UsageError(message)

    provisioning = [
        i for i in items if i.get_closest_marker("e2e") or i.get_closest_marker("integration")
    ]
    if provisioning:
        config._run_lock = acquire_run_lock()
        log_line(f"acquired run lock {_lock_path()}")


def pytest_unconfigure(config):
    if getattr(config, "_run_lock", None):
        release_run_lock()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    stats = {key: len(value) for key, value in terminalreporter.stats.items() if key}
    log_line(f"=== session end: exit={exitstatus} {stats}")
    for report in terminalreporter.stats.get("skipped", []):
        log_line(f"SKIPPED {report.nodeid}: {report.longrepr}")
    for key in ("failed", "error"):
        for report in terminalreporter.stats.get(key, []):
            log_line(f"{key.upper()} {report.nodeid}:\n{report.longreprtext}")
    print(f"\nrun log: {log_path()}")


@pytest.fixture(scope="module")
def ec2():
    boto3 = pytest.importorskip("boto3")
    return boto3.client("ec2", region_name=os.environ["AWS_REGION"])


@pytest.fixture
def byovpc(request):
    """Bring the byovpc fixture stack up for one mode and yield its stack outputs."""
    mode = request.param
    stack = stack_name(mode)
    region = os.environ["AWS_REGION"]
    azs = request.config.getini("byovpc_azs")

    if not (FIXTURE_DIR / "Pulumi.yaml").exists():
        pytest.skip(f"byovpc fixture not found at {FIXTURE_DIR}")

    pulumi("stack", "select", "--create", stack)
    pulumi("config", "set", "aws:region", region)
    # remove the list first: setting azs[0..n] leaves any longer previous list in
    # place, so a stack configured with more zones would silently keep them
    subprocess.run(
        ["pulumi", "config", "rm", "byovpc:azs"],
        cwd=FIXTURE_DIR,
        capture_output=True,
        check=False,
    )
    for i, az in enumerate(azs.split(",")):
        pulumi("config", "set", "--path", f"byovpc:azs[{i}]", az.strip())

    pulumi("install")
    pulumi("up", "--yes", "--skip-preview")
    outputs = pulumi_json("stack", "output", "--json")
    outputs["stack"] = stack
    try:
        yield outputs
    finally:
        if keep_stacks(request):
            message = f"leaving byovpc stack {stack} up ({outputs.get('vpc_id')}) - destroy it with: pulumi destroy --yes --cwd {FIXTURE_DIR} --stack {stack}"
            print(f"\n{message}")
            log_line(message)
        else:
            pulumi("destroy", "--yes", "--skip-preview")
