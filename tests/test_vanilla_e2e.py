import pytest
from e2e.deploy import deployed_project
from e2e.reachability import assert_answers, cell_fqdn, data_plane_host

pytestmark = pytest.mark.e2e


@pytest.fixture
def vanilla_project(request):
    yield from deployed_project(request, "vanilla")


def test_e2e_vanilla(vanilla_project):
    assert_answers(data_plane_host(cell_fqdn(vanilla_project)))
