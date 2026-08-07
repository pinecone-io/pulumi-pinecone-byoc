"""Shared naming conventions and constants for BYOC clusters."""

import re

import pulumi

from .providers import Environment

ORG_NAME_MAX_LENGTH = 16

# CNAME records created in both DNS and NLB components across all clouds
DNS_CNAMES = ["*.svc", "metrics", "prometheus"]

UNRESOLVED_CELL = "unresolved-byoc-0000"


def cell(org_name: str | None, env_name: str | None) -> str:
    """Derive cell name from an org and environment: e.g. pinecone-byoc-ef7a"""
    if not org_name or not env_name:
        return UNRESOLVED_CELL
    org = re.sub(r"[^a-z0-9]", "", org_name.lower())[:ORG_NAME_MAX_LENGTH]
    return f"{org}-byoc-{env_name.split('.')[0][-4:]}"


def cell_name(environment: Environment) -> pulumi.Output[str]:
    return pulumi.Output.all(environment.org_name, environment.env_name).apply(
        lambda args: cell(args[0], args[1])
    )
