import ipaddress
import logging
import os
import subprocess
import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "setup"))

from e2e import log_config, settings  # noqa: E402
from e2e.aws import TEST_TAG, delete_vpc  # noqa: E402
from e2e.commands import pulumi, pulumi_json  # noqa: E402
from e2e.settings import e2e_azs, keep_stacks  # noqa: E402
from e2e.stacks import destroy_stack, stack_name  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "byovpc"


def pytest_addoption(parser):
    settings.add_options(parser)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report


def pytest_configure(config):
    log_config.start(config.option.keyword or config.option.markexpr or "all")
    region = settings.apply_to_environment(config)
    logging.info(
        f"=== session start: -m {config.option.markexpr!r} -k {config.option.keyword!r} "
        f"profile={os.environ.get('AWS_PROFILE', '<ambient credentials>')} region={region} "
        f"azs={settings.e2e_azs(config)} "
        f"control-plane={os.environ.get('PINECONE_GLOBAL_ENV', 'prod (module default)')}"
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    logging.info(f"selected {len(items)} test(s): {[i.name for i in items]}")
    needs_api_key = [
        i for i in items if i.get_closest_marker("e2e") or i.get_closest_marker("destroy")
    ]
    if needs_api_key and not os.environ.get("PINECONE_API_KEY"):
        message = (
            "PINECONE_API_KEY must be set to run e2e tests; nothing was provisioned or "
            "destroyed. Export it and re-run."
        )
        logging.info(f"ABORT {message}")
        raise pytest.UsageError(message)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    stats = {key: len(value) for key, value in terminalreporter.stats.items() if key}
    logging.info(f"=== session end: exit={exitstatus} {stats}")
    for report in terminalreporter.stats.get("skipped", []):
        logging.info(f"SKIPPED {report.nodeid}: {report.longrepr}")
    for key in ("failed", "error"):
        for report in terminalreporter.stats.get(key, []):
            logging.info(f"{key.upper()} {report.nodeid}:\n{report.longreprtext}")
    print(f"\nrun log: {log_config.log_path()}")


@pytest.fixture(scope="module")
def ec2():
    return boto3.client("ec2", region_name=os.environ["AWS_REGION"])


@pytest.fixture
def customer_vpc(request):
    """A stand-in customer VPC with one CIDR, no subnets and no egress.

    This is the whole byovpc fixture for shapes that never route a packet: a
    targeted network apply creates subnets and stops, so there is nothing for a
    NAT to serve and nothing that needs the fixture's Pulumi program.
    """
    client = boto3.client("ec2", region_name=os.environ["AWS_REGION"])

    name = stack_name("customer-vpc")
    vpc_cidr = "10.0.0.0/16"
    created = client.create_vpc(
        CidrBlock=vpc_cidr,
        TagSpecifications=[
            {
                "ResourceType": "vpc",
                "Tags": [{"Key": "Name", "Value": name}, {"Key": TEST_TAG, "Value": name}],
            }
        ],
    )
    vpc_id = created["Vpc"]["VpcId"]
    logging.info("created stand-in customer VPC %s %s as %s", vpc_id, vpc_cidr, name)
    for attribute in ("EnableDnsSupport", "EnableDnsHostnames"):
        client.modify_vpc_attribute(VpcId=vpc_id, **{attribute: {"Value": True}})

    network = ipaddress.ip_network(vpc_cidr)
    carve_cidr = f"{network.network_address + (1 << 16)}/16"

    try:
        yield {
            "vpc_id": vpc_id,
            "vpc_cidr": vpc_cidr,
            "carve_cidr": carve_cidr,
            "azs": e2e_azs(request.config),
        }
    finally:
        if keep_stacks(request):
            logging.info(
                "leaving stand-in customer VPC %s up - delete it with: "
                "aws ec2 delete-vpc --vpc-id %s --region %s",
                vpc_id,
                vpc_id,
                os.environ["AWS_REGION"],
            )
        else:
            delete_vpc(client, vpc_id)


@pytest.fixture
def byovpc(request):
    mode = request.param
    stack = stack_name(mode)
    region = os.environ["AWS_REGION"]
    azs = e2e_azs(request.config)

    if not (FIXTURE_DIR / "Pulumi.yaml").exists():
        pytest.skip(f"byovpc fixture not found at {FIXTURE_DIR}")

    pulumi("stack", "select", "--create", stack, cwd=FIXTURE_DIR)
    pulumi("config", "set", "aws:region", region, cwd=FIXTURE_DIR)
    subprocess.run(
        ["pulumi", "config", "rm", "byovpc:azs"],
        cwd=FIXTURE_DIR,
        capture_output=True,
        check=False,
    )
    for i, az in enumerate(azs.split(",")):
        pulumi("config", "set", "--path", f"byovpc:azs[{i}]", az.strip(), cwd=FIXTURE_DIR)

    pulumi("install", cwd=FIXTURE_DIR)
    pulumi("up", "--yes", "--skip-preview", cwd=FIXTURE_DIR)
    outputs = pulumi_json("stack", "output", "--json", cwd=FIXTURE_DIR)
    outputs["stack"] = stack
    try:
        yield outputs
    finally:
        if keep_stacks(request):
            logging.info(
                "leaving byovpc stack %s up (%s) - destroy it with: "
                "pulumi destroy --yes --cwd %s --stack %s",
                stack,
                outputs.get("vpc_id"),
                FIXTURE_DIR,
                stack,
            )
        else:
            destroy_stack(FIXTURE_DIR, stack)
