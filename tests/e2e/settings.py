import os

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


def keep_stacks(request):
    if request.config.getoption("--keep"):
        return True
    if not request.config.getoption("--keep-failed"):
        return False
    return any(
        getattr(getattr(request.node, f"rep_{phase}", None), "failed", False)
        for phase in ("setup", "call")
    )
