import os
from typing import NamedTuple

from .stacks import stack_name

AMBIENT_AWS_CREDENTIALS = (
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_ACCESS_KEY_ID",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)

CONTROL_PLANE_INI = {
    "pinecone_global_env": "PINECONE_GLOBAL_ENV",
    "pinecone_api_url": "PINECONE_API_URL",
    "pinecone_auth0_domain": "PINECONE_AUTH0_DOMAIN",
    "pinecone_gcp_project": "PINECONE_GCP_PROJECT",
}


def add_options(parser):
    parser.addini("aws_profile", "AWS profile used by integration tests", default="byoc-dev")
    parser.addini("aws_region", "AWS region used by integration tests", default="us-east-2")
    parser.addini(
        "e2e_azs",
        "comma-separated AZs the e2e deploy uses",
        default="us-east-2a,us-east-2b",
    )
    for ini_key in CONTROL_PLANE_INI:
        parser.addini(ini_key, f"control plane setting passed to the wizard as {ini_key.upper()}")
    parser.addoption(
        "--keep",
        action="store_true",
        default=False,
        help="leave everything the test provisioned up: the cluster, its database, its network",
    )
    parser.addoption(
        "--keep-failed",
        action="store_true",
        default=False,
        help="leave provisioned stacks up only when the test fails, so it can be inspected",
    )
    parser.addoption(
        "--destroy-stack",
        action="append",
        default=[],
        metavar="STACK",
        help="stack the destroy test tears down; repeat to sweep several. "
        "Defaults to the stack this run's E2E_STACK_PREFIX would have created.",
    )
    parser.addoption(
        "--grant-cluster-access",
        action="store_true",
        default=False,
        help="before destroying, give the calling principal cluster-admin on the stack's EKS "
        "cluster. The module creates no access entries, so a CI-built cluster's only admin is "
        "the role that created it and a local destroy cannot reach the Kubernetes API.",
    )
    parser.addoption(
        "--destroy-cloud",
        choices=("aws", "gcp", "azure"),
        default=None,
        help="cloud for --destroy-stack, when the stack name does not say which",
    )
    parser.addoption(
        "--destroy-region",
        default=None,
        metavar="REGION",
        help="region for --destroy-stack, when it is not the region under test",
    )


def aws_region(config):
    return os.environ.get("AWS_REGION") or config.getini("aws_region")


def e2e_azs(config):
    return os.environ.get("PINECONE_AZS") or config.getini("e2e_azs")


def apply_to_environment(config):
    """Put the resolved settings where the wizard, pulumi and boto3 will find them."""
    if not any(os.environ.get(v) for v in AMBIENT_AWS_CREDENTIALS):
        os.environ.setdefault("AWS_PROFILE", config.getini("aws_profile"))
    region = aws_region(config)
    os.environ["AWS_REGION"] = region
    os.environ["AWS_DEFAULT_REGION"] = region

    for ini_key, env in CONTROL_PLANE_INI.items():
        value = config.getini(ini_key)
        if value:
            os.environ.setdefault(env, value)
    return region


class DestroyTarget(NamedTuple):
    stack: str
    cloud: str
    region: str


def _cloud_of(stack, override):
    if override:
        return override
    for cloud in ("aws", "gcp", "azure"):
        if f"-{cloud}-" in f"-{stack}-":
            return cloud
    return "aws"


def destroy_targets(config):
    # every shape a run can deploy; the destroy test skips the ones that are not there,
    # so the teardown job needs no flag telling it which shape ran
    stacks = config.getoption("--destroy-stack") or [
        stack_name("vanilla", "byoc"),
        stack_name("private", "byoc"),
        stack_name("byovpc", "byoc"),
    ]
    cloud_override = config.getoption("--destroy-cloud")
    region = config.getoption("--destroy-region") or aws_region(config)
    return [DestroyTarget(s, _cloud_of(s, cloud_override), region) for s in stacks]


def remember_report(item, report) -> None:
    """Record a phase's report on the test, and a failure on everything above it.

    A fixture asks about the node it is scoped to, and only a function ever runs a
    phase - so a module-scoped fixture holding a cluster up would see nothing and
    tear it down whatever happened in it.
    """
    setattr(item, f"rep_{report.when}", report)
    if report.failed:
        for node in item.listchain():
            setattr(node, f"rep_{report.when}", report)


def keep_stacks(request):
    if request.config.getoption("--keep"):
        return True
    if not request.config.getoption("--keep-failed"):
        return False
    return any(
        getattr(getattr(request.node, f"rep_{phase}", None), "failed", False)
        for phase in ("setup", "call")
    )
