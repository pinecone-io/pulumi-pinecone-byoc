import pytest
from e2e.commands import pulumi_json
from e2e.deploy import deployed_project
from e2e.reachability import assert_answers, data_plane_host

pytestmark = pytest.mark.e2e


@pytest.fixture
def vanilla_project(request):
    yield from deployed_project(request, "vanilla")


def test_e2e_vanilla(vanilla_project):
    environment = pulumi_json("stack", "output", "--json", cwd=vanilla_project).get("environment")
    assert environment, "the deploy exported no environment"
    assert_answers(data_plane_host(environment))
