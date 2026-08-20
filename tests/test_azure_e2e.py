import pytest
from e2e.commands import pulumi_json
from e2e.deploy import deployed_project

pytestmark = [pytest.mark.e2e, pytest.mark.azure]


@pytest.fixture
def azure_project(request):
    yield from deployed_project(
        request,
        "vanilla",
        cloud="azure",
        PINECONE_VPC_CIDR="10.0.0.0/16",
        PINECONE_PUBLIC_ACCESS="false",
    )


def test_e2e_azure(azure_project):
    outputs = pulumi_json("stack", "output", "--json", cwd=azure_project)

    assert outputs.get("environment"), "the deploy exported no environment"
