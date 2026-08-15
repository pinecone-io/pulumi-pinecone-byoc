import contextlib
import logging
import os
import shutil
import subprocess
import sys
import threading

import pytest
from e2e.commands import pulumi, pulumi_json, run
from e2e.indexes import delete_project_indexes
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS, REPO_ROOT
from e2e.settings import e2e_azs, keep_stacks
from e2e.stacks import destroy_stack, find_stack, stack_name
from e2e.wizard import generate_project

STATEFUL = (
    "aws:ec2/vpc:Vpc",
    "aws:ec2/subnet:Subnet",
    "aws:eks/cluster:Cluster",
    "aws:rds/cluster:Cluster",
    "aws:rds/clusterInstance:ClusterInstance",
    "aws:s3/bucket:Bucket",
    "aws:s3/bucketV2:BucketV2",
    "aws:kms/key:Key",
    "aws:secretsmanager/secret:Secret",
)
DESTRUCTIVE = ("replace", "create-replacement", "delete-replaced", "replace-delete", "delete")

SOURCE_PINECONE_VERSION = os.environ.get("UPGRADE_SOURCE_VERSION", "main-8c39fad")


def baseline_ref():
    if os.environ.get("UPGRADE_BASELINE_REF"):
        return os.environ["UPGRADE_BASELINE_REF"]
    tags = subprocess.run(
        ["git", "tag", "-l", "v*", "--sort=-v:refname"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    latest = tags.stdout.split("\n")[0].strip() if tags.returncode == 0 else ""
    if not latest:
        raise pytest.UsageError(
            "no release tag to upgrade from; set UPGRADE_BASELINE_REF to a ref this "
            "checkout has (git fetch --tags)"
        )
    return latest


def resolve(ref):
    subprocess.run(
        ["git", "fetch", "--quiet", "origin", ref], cwd=REPO_ROOT, check=False, capture_output=True
    )
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        raise pytest.UsageError(
            f"cannot resolve baseline ref {ref!r} to a commit; "
            "set UPGRADE_BASELINE_REF to something this checkout has"
        )
    return resolved.stdout.strip()


def plan_steps(project_dir):
    plan = pulumi_json("preview", "--json", cwd=project_dir)
    return [step for step in plan.get("steps", []) if step.get("urn")]


def describe(steps):
    counted = {}
    for step in steps:
        counted[step["op"]] = counted.get(step["op"], 0) + 1
    return ", ".join(f"{op}={count}" for op, count in sorted(counted.items())) or "nothing"


def detail(steps):
    lines = []
    for step in steps:
        if step["op"] == "same":
            continue
        changed = ", ".join(step.get("diffs") or []) or "(no property diff reported)"
        lines.append(f"  {step['op']:<8} {step['urn'].split('::')[-1]:<34} {changed}")
    return "\n".join(lines) or "  nothing"


CONTROL_PLANE_ARGS = {
    "global_env": "PINECONE_GLOBAL_ENV",
    "api_url": "PINECONE_API_URL",
    "auth0_domain": "PINECONE_AUTH0_DOMAIN",
    "gcp_project": "PINECONE_GCP_PROJECT",
}


def _passes(arg, generated):
    # a generator recent enough to know the argument spells it either as a literal
    # keyword or as a key of the control_plane dict it splats into the call
    return f"{arg}=" in generated or f'"{arg}":' in generated


def teach_the_baseline_its_control_plane(project_dir, source, stack):
    program = project_dir / "__main__.py"
    generated = program.read_text()
    accepted = (source / "pulumi_pinecone_byoc" / "aws" / "cluster.py").read_text()
    missing = {
        arg: os.environ[env]
        for arg, env in CONTROL_PLANE_ARGS.items()
        if not _passes(arg, generated) and f"    {arg}:" in accepted and os.environ.get(env)
    }
    if not missing:
        logging.info("baseline already carries its own control plane settings")
        return

    anchor = '        tags=config.get_object("tags"),'
    assert anchor in generated, f"cannot find where to inject control plane args in {program}"
    injected = "\n".join(f'        {arg}=config.get("{arg.replace("_", "-")}"),' for arg in missing)
    program.write_text(generated.replace(anchor, f"{anchor}\n{injected}", 1))
    for arg, value in missing.items():
        pulumi("config", "set", arg.replace("_", "-"), value, "--stack", stack, cwd=project_dir)
    logging.info(f"taught the baseline about {', '.join(missing)}")


def pin_published_module(project_dir, ref):
    version = ref.lstrip("v")
    if not version[:1].isdigit():
        logging.info(f"baseline {ref} is not a version tag; leaving the module unpinned")
        return
    pyproject = project_dir / "pyproject.toml"
    pinned = pyproject.read_text().replace(
        '"pulumi-pinecone-byoc[aws]"', f'"pulumi-pinecone-byoc[aws]=={version}"'
    )
    pyproject.write_text(pinned)
    logging.info(f"baseline pinned to pulumi-pinecone-byoc=={version}")


def install_the_source_version(project_dir, stack):
    pulumi(
        "config",
        "set",
        "pinecone-version",
        SOURCE_PINECONE_VERSION,
        "--stack",
        stack,
        cwd=project_dir,
    )
    logging.info(f"baseline installs pinecone {SOURCE_PINECONE_VERSION}")


def upgrade_in_place(baseline_dir, candidate_dir, stack):
    shutil.rmtree(candidate_dir, ignore_errors=True)
    shutil.copytree(
        baseline_dir,
        candidate_dir,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.log"),
    )
    run(
        sys.executable,
        "setup/wizard.py",
        "--upgrade",
        "--output-dir",
        str(candidate_dir),
        "--stack-name",
        stack,
        "--dev",
        str(REPO_ROOT),
        cwd=REPO_ROOT,
    )
    run("uv", "sync", cwd=candidate_dir)
    pulumi("stack", "select", stack, cwd=candidate_dir)


@contextlib.contextmanager
def streaming_pinetools_logs():
    stop = threading.Event()
    streamer = threading.Thread(target=supervise_pinetools_logs, args=(None, stop), daemon=True)
    streamer.start()
    try:
        yield
    finally:
        stop.set()
        streamer.join(timeout=10)


@pytest.fixture(scope="session")
def baseline_source():
    sha = resolve(baseline_ref())
    path = PROJECTS / f"baseline-{sha[:12]}"
    run("git", "worktree", "add", "--force", "--detach", str(path), sha, cwd=REPO_ROOT)
    logging.info(f"upgrading from {baseline_ref()} = {sha}")
    try:
        yield path
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )


@pytest.fixture(scope="session")
def baseline(pytestconfig, baseline_source):
    """The baseline project, generated from inputs alone.

    Installing and upgrading are separate jobs on separate runners, so this
    runs twice against one deployment. Every answer here is a constant or comes
    from the environment, and the wizard selects the stack it finds rather than
    creating a second one, so the second run reproduces the project the first
    one deployed instead of carrying it between machines.
    """
    stack = stack_name("vanilla", "byoc")
    env = {
        "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
        "PINECONE_REGION": os.environ["AWS_REGION"],
        "PINECONE_AZS": e2e_azs(pytestconfig),
        "PINECONE_VPC_CIDR": "10.0.0.0/16",
        "PINECONE_PUBLIC_ACCESS": "true",
        "PINECONE_PROJECT_NAME": stack,
        "PINECONE_DELETION_PROTECTION": "false",
    }

    baseline_dir = generate_project(
        PROJECTS / f"{stack}-baseline",
        stack,
        "aws",
        env,
        source=baseline_source,
        dev=False,
    )
    pin_published_module(baseline_dir, baseline_ref())
    teach_the_baseline_its_control_plane(baseline_dir, baseline_source, stack)
    install_the_source_version(baseline_dir, stack)
    return baseline_dir, stack


@pytest.mark.upgrade
@pytest.mark.baseline
def test_baseline_vanilla_installs(baseline):
    baseline_dir, stack = baseline
    with streaming_pinetools_logs():
        pulumi("up", "--yes", "--skip-preview", cwd=baseline_dir)
    logging.info(f"baseline {stack} is up; the upgrade runs against it")


@pytest.mark.upgrade
def test_upgrade_vanilla(request, baseline):
    baseline_dir, stack = baseline
    assert find_stack(stack), (
        f"{stack} is not deployed; the upgrade upgrades a running baseline, so run "
        '`pytest -m "upgrade and baseline"` first (in CI, the up job)'
    )

    candidate_dir = PROJECTS / f"{stack}-candidate"
    upgrade_in_place(baseline_dir, candidate_dir, stack)

    try:
        planned = plan_steps(candidate_dir)
        logging.info(f"upgrade plan: {describe(planned)}")
        logging.info(f"upgrade plan detail:\n{detail(planned)}")

        destructive = [
            f"{step['op']} {step['urn']}"
            for step in planned
            if step["op"] in DESTRUCTIVE and step["urn"].split("::")[2].split("$")[-1] in STATEFUL
        ]
        assert not destructive, (
            "upgrading must not take the deployment's state with it:\n" + "\n".join(destructive)
        )

        with streaming_pinetools_logs():
            pulumi("up", "--yes", "--skip-preview", cwd=candidate_dir)

        settled = [step for step in plan_steps(candidate_dir) if step["op"] != "same"]
        assert not settled, (
            "the upgraded program does not converge; a second preview still wants:\n"
            + ("\n".join(f"{step['op']} {step['urn']}" for step in settled))
        )
    finally:
        if keep_stacks(request):
            message = (
                f"leaving upgrade stack {stack} up - destroy it with: "
                f"cd {candidate_dir} && pulumi destroy --yes"
            )
            print(f"\n{message}")
            logging.info(message)
        else:
            delete_project_indexes(candidate_dir)
            destroy_stack(candidate_dir)
