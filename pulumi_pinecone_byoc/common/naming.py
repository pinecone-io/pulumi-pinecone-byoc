"""Shared naming conventions for BYOC clusters."""

import re

import pulumi

from .providers import Environment

ORG_NAME_MAX_LENGTH = 16


def cell_name(environment: Environment) -> pulumi.Output[str]:
    """Derive cell name from environment: e.g. pinecone-byoc-ef7a"""

    def sanitize(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())[:ORG_NAME_MAX_LENGTH]

    return pulumi.Output.all(environment.org_name, environment.env_name).apply(
        lambda args: f"{sanitize(args[0])}-byoc-{args[1].split('.')[0][-4:]}"
    )
