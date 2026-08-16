import botocore.exceptions
import pytest

from pulumi_pinecone_byoc.aws import vpc_perms


class Ec2:
    def __init__(self, answers):
        self.answers = answers
        self.asked: list[tuple[str, dict]] = []

    def __getattr__(self, operation):
        def call(**kwargs):
            self.asked.append((operation, kwargs))
            answer = self.answers.get(operation, "DryRunOperation")
            if isinstance(answer, str):
                raise botocore.exceptions.ClientError({"Error": {"Code": answer}}, operation)
            raise answer

        return call

    def probed(self, operation):
        return [kwargs for name, kwargs in self.asked if name == operation]


@pytest.fixture
def ec2(monkeypatch):
    """An ec2 client that answers a dry run without a credential or a network."""

    def answering(**answers):
        client = Ec2(answers)
        monkeypatch.setattr(vpc_perms.boto3, "client", lambda *args, **kwargs: client)
        return client

    return answering
