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
from e2e.stacks import destroy_stack, stack_name
from e2e.wizard import generate_project

pytestmark = pytest.mark.upgrade

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


def teach_the_baseline_its_control_plane(project_dir, source, stack):
    program = project_dir / "__main__.py"
    generated = program.read_text()
    accepted = (source / "pulumi_pinecone_byoc" / "aws" / "cluster.py").read_text()
    missing = {
        arg: os.environ[env]
        for arg, env in CONTROL_PLANE_ARGS.items()
        if f"{arg}=" not in generated and f"    {arg}:" in accepted and os.environ.get(env)
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


@pytest.fixture
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


@pytest.fixture
def upgraded(request, baseline_source):
    stack = stack_name("vanilla", "upgrade")
    env = {
        "PINECONE_API_KEY": os.environ["PINECONE_API_KEY"],
        "PINECONE_REGION": os.environ["AWS_REGION"],
        "PINECONE_AZS": e2e_azs(request.config),
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
    candidate_dir = PROJECTS / f"{stack}-candidate"

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs, args=(None, stop_streaming), daemon=True
    )
    streamer.start()

    try:
        pulumi("up", "--yes", "--skip-preview", cwd=baseline_dir)
        upgrade_in_place(baseline_dir, candidate_dir, stack)
        yield candidate_dir
    finally:
        try:
            if keep_stacks(request):
                message = (
                    f"leaving upgrade stack {stack} up - destroy it with: "
                    f"cd {candidate_dir} && pulumi destroy --yes"
                )
                print(f"\n{message}")
                logging.info(message)
            else:
                target = candidate_dir if (candidate_dir / "Pulumi.yaml").exists() else baseline_dir
                delete_project_indexes(target)
                destroy_stack(target)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


def test_upgrade_vanilla(upgraded):
    planned = plan_steps(upgraded)
    logging.info(f"upgrade plan: {describe(planned)}")
    logging.info(f"upgrade plan detail:\n{detail(planned)}")

    destructive = [
        f"{step['op']} {step['urn']}"
        for step in planned
        if step["op"] in DESTRUCTIVE and step["urn"].split("::")[2].split("$")[-1] in STATEFUL
    ]
    assert not destructive, "upgrading must not take the deployment's state with it:\n" + "\n".join(
        destructive
    )

    pulumi("up", "--yes", "--skip-preview", cwd=upgraded)

    settled = [step for step in plan_steps(upgraded) if step["op"] != "same"]
    assert not settled, (
        "the upgraded program does not converge; a second preview still wants:\n"
        + ("\n".join(f"{step['op']} {step['urn']}" for step in settled))
    )
