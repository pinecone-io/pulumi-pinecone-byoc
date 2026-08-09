import pytest
from e2e import stacks

STACK = "pinecone/ci-aws-pr74-6f4ee1b-vanilla-byoc/ci-aws-pr74-6f4ee1b-vanilla-byoc"
CI = "166884686556"
DEV = "302263084545"


def test_refuses_when_the_caller_is_a_different_account(monkeypatch):
    monkeypatch.setattr(stacks, "stack_accounts", lambda qualified: {CI})
    monkeypatch.setattr(stacks, "caller_account", lambda: DEV)

    with pytest.raises(AssertionError) as excinfo:
        stacks.refuse_foreign_account(STACK)

    message = str(excinfo.value)
    assert CI in message
    assert DEV in message
    assert "AWS_PROFILE" in message


def test_allows_the_account_that_owns_the_stack(monkeypatch):
    monkeypatch.setattr(stacks, "stack_accounts", lambda qualified: {CI})
    monkeypatch.setattr(stacks, "caller_account", lambda: CI)

    stacks.refuse_foreign_account(STACK)


def test_an_empty_stack_names_no_account_so_nothing_is_checked(monkeypatch):
    monkeypatch.setattr(stacks, "stack_accounts", lambda qualified: set())
    monkeypatch.setattr(
        stacks, "caller_account", lambda: pytest.fail("must not call STS with nothing to compare")
    )

    stacks.refuse_foreign_account(STACK)


def test_accounts_are_read_from_the_arns_in_state(monkeypatch):
    export = {
        "deployment": {
            "resources": [
                {"urn": "urn:pulumi:s::p::aws:ec2/vpc:Vpc::pc-vpc", "id": "vpc-0d44357ef3ae39236"},
                {"outputs": {"arn": f"arn:aws:ec2:us-east-2:{CI}:vpc/vpc-0d44357ef3ae39236"}},
                {"outputs": {"arn": f"arn:aws:iam::{CI}:role/pc-eks-cluster"}},
            ]
        }
    }
    monkeypatch.setattr(stacks, "pulumi_json", lambda *args, **kwargs: export)

    assert stacks.stack_accounts(STACK) == {CI}
