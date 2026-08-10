"""Deploy a whole cluster into a VPC the module did not create.

The network suite proves the subnets land correctly; this proves a cluster can
live in them - nodes reaching the registry through egress that is not ours, an
internet-facing load balancer behind the customer's gateway, and the data plane
answering from outside their VPC. It is the only tier that can show any of that,
and it takes the better part of an hour.

The VPC it deploys into is the one tests/vpc/program builds in module shape: by
then it has subnets, per-AZ route tables and a NAT, which is what the module has
to find in a customer's account. Deselected by default; needs PINECONE_API_KEY.

    pytest -m e2e tests/vpc/test_vpc_e2e.py -s

Runs take tens of minutes, so prefix with `caffeinate -i` on macOS. Teardown of an
interrupted run is the BYOC stack first, then the stand-in:

    cd .e2e/$USER-byovpc-byoc && pulumi destroy --yes
    cd tests/vpc/program && pulumi destroy --yes --stack $USER-byovpc-vpc
"""

import logging
import os
import threading

import pytest
from e2e.commands import pulumi, pulumi_json
from e2e.installer import supervise_pinetools_logs
from e2e.paths import PROJECTS, REPO_ROOT
from e2e.reachability import assert_data_plane_answers, assert_never_answers, data_plane_host
from e2e.settings import e2e_azs, keep_stacks
from e2e.stacks import destroy_stack, stack_name
from e2e.wizard import generate_project, non_interactive_env

pytestmark = pytest.mark.e2e

PROGRAM_DIR = REPO_ROOT / "tests" / "vpc" / "program"


@pytest.fixture(scope="module")
def their_vpc(request):
    """A VPC the module did not create, made the cheap way: itself, one shape over."""
    stack = stack_name("byovpc", "vpc")
    azs = [az.strip() for az in e2e_azs(request.config).split(",")]

    pulumi("stack", "select", "--create", stack, cwd=PROGRAM_DIR)
    pulumi("config", "set", "aws:region", os.environ["AWS_REGION"], cwd=PROGRAM_DIR)
    pulumi("config", "set", "region", os.environ["AWS_REGION"], cwd=PROGRAM_DIR)
    pulumi("config", "set", "vpc-cidr", "10.0.0.0/16", cwd=PROGRAM_DIR)
    for index, az in enumerate(azs):
        pulumi("config", "set", "--path", f"availability-zones[{index}]", az, cwd=PROGRAM_DIR)

    try:
        pulumi("up", "--yes", "--skip-preview", "--stack", stack, cwd=PROGRAM_DIR)
        outputs = pulumi_json("stack", "output", "--json", "--stack", stack, cwd=PROGRAM_DIR)
        yield {"vpc_id": outputs["vpc_id"], "azs": azs}
    finally:
        if keep_stacks(request):
            logging.info(
                "leaving stand-in VPC stack %s up - destroy it with: cd %s && "
                "pulumi destroy --yes --stack %s",
                stack,
                PROGRAM_DIR,
                stack,
            )
        else:
            destroy_stack(PROGRAM_DIR, stack)


@pytest.fixture(scope="module")
def byoc_in_their_vpc(request, their_vpc):
    shape = getattr(request, "param", "byovpc")
    stack = stack_name(shape, "byoc")
    public_access = os.environ.get("PINECONE_PUBLIC_ACCESS", "true")
    project_dir = generate_project(
        PROJECTS / stack,
        stack,
        "aws",
        non_interactive_env(
            request.config,
            os.environ["AWS_REGION"],
            stack,
            PINECONE_AZS=",".join(their_vpc["azs"]),
            PINECONE_EXISTING_VPC_ID=their_vpc["vpc_id"],
            PINECONE_VPC_CIDR="10.1.0.0/16",
            PINECONE_PUBLIC_ACCESS=public_access,
        ),
    )

    stop_streaming = threading.Event()
    streamer = threading.Thread(
        target=supervise_pinetools_logs,
        args=(their_vpc["vpc_id"], stop_streaming),
        daemon=True,
    )
    streamer.start()

    try:
        pulumi("up", "--yes", "--skip-preview", cwd=project_dir)
        yield {
            "project_dir": project_dir,
            "public_access": public_access == "true",
            "shape": shape,
        }
    finally:
        try:
            if keep_stacks(request):
                logging.info(
                    "leaving BYOC stack %s up - destroy it with: cd %s && "
                    "caffeinate -i pulumi destroy --yes",
                    stack,
                    project_dir,
                )
            else:
                destroy_stack(project_dir)
        finally:
            stop_streaming.set()
            streamer.join(timeout=10)


@pytest.mark.parametrize("byoc_in_their_vpc", ["byovpc"], indirect=True)
def test_a_cluster_deployed_into_their_vpc_is_reachable_as_the_shape_asked(byoc_in_their_vpc):
    project_dir = byoc_in_their_vpc["project_dir"]

    if byoc_in_their_vpc["public_access"]:
        assert_data_plane_answers(project_dir)
        return

    environment = pulumi_json("stack", "output", "--json", cwd=project_dir).get("environment")
    assert environment, "the deploy exported no environment"
    assert_never_answers(data_plane_host(environment))


@pytest.mark.parametrize("byoc_in_their_vpc", ["byovpc"], indirect=True)
def test_the_module_built_no_vpc_and_no_egress_of_its_own(byoc_in_their_vpc, their_vpc):
    """The cluster is theirs to host: we add subnets, not a network.

    A deploy that quietly created its own VPC would still pass a reachability
    probe, so the shape is asserted rather than inferred from the cluster working.
    """
    state = pulumi_json("stack", "export", cwd=byoc_in_their_vpc["project_dir"])
    resources = state.get("deployment", {}).get("resources", [])
    types = [resource["type"] for resource in resources]

    assert "aws:ec2/vpc:Vpc" not in types, "the customer's VPC is the one it should have used"
    assert "aws:ec2/natGateway:NatGateway" not in types, "egress in their VPC is theirs to route"

    vpc_ids = {
        resource["outputs"]["vpcId"]
        for resource in resources
        if resource["type"] == "aws:ec2/subnet:Subnet" and "outputs" in resource
    }
    assert vpc_ids == {their_vpc["vpc_id"]}, f"subnets landed outside their VPC: {vpc_ids}"
