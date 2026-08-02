import pytest

from pulumi_pinecone_byoc.common.naming import ORG_NAME_MAX_LENGTH, UNRESOLVED_CELL, cell


def test_a_cell_is_the_org_and_the_environments_first_label():
    assert cell("Pinecone Inc", "ef7a.byoc.pinecone.io") == "pineconeinc-byoc-ef7a"


def test_the_org_is_sanitised_and_capped():
    assert cell("A Very Long Org Name Ltd.", "2cea.byoc.pinecone.io") == (
        "averylongorgname-byoc-2cea"
    )
    assert len("averylongorgname") == ORG_NAME_MAX_LENGTH


@pytest.mark.parametrize(
    ("org", "env"),
    [(None, "ef7a.byoc.pinecone.io"), ("Pinecone", None), (None, None), ("", "")],
)
def test_a_skipped_environment_does_not_crash_the_program(org, env):
    """`pulumi up --target` hands back empty outputs for what it skipped."""
    assert cell(org, env) == UNRESOLVED_CELL
