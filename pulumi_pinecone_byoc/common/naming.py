"""Shared naming conventions and constants for BYOC clusters."""

import re

import pulumi

from .providers import Environment

ORG_NAME_MAX_LENGTH = 16

# CNAME records created in both DNS and NLB components across all clouds
DNS_CNAMES = ["*.svc", "metrics", "prometheus"]

CERTIFICATE_NAME_MAX_LENGTH = 64

PINECONE_HOSTED_DOMAIN = "pinecone.io"


def cell_name(environment: Environment) -> pulumi.Output[str]:
    """Derive cell name from environment: e.g. pinecone-byoc-ef7a"""

    def sanitize(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())[:ORG_NAME_MAX_LENGTH]

    return pulumi.Output.all(environment.org_name, environment.env_name).apply(
        lambda args: f"{sanitize(args[0])}-byoc-{args[1].split('.')[0][-4:]}"
    )


def refuse_a_domain_only_aws_can_be_delegated(domain: str, cloud: str) -> None:
    if domain != PINECONE_HOSTED_DOMAIN:
        raise ValueError(
            f"a {cloud} cell resolves under {PINECONE_HOSTED_DOMAIN}. Only aws can answer "
            f"on a domain Pinecone does not host: its DNS component waits for the "
            f"delegation the owner of {domain} has to make, where {cloud}'s still asks the "
            f"control plane to write one, which it will refuse for a zone it does not host. "
            f"Unset the domain config key."
        )


def refuse_a_domain_no_certificate_can_cover(domain: str, region: str, global_env: str) -> None:
    prefix = "" if global_env == "prod" else f"{global_env}-"
    longest_cell = f"{prefix}aws-{region}-ab12.byoc"
    budget = CERTIFICATE_NAME_MAX_LENGTH - len(f"*.svc.private.{longest_cell}.")
    if len(domain) > budget:
        raise ValueError(
            f"{domain} is {len(domain)} characters, and at most {budget} fit here: a "
            f"cell in {region} is named {longest_cell}, and the certificate that covers "
            f"it may not exceed {CERTIFICATE_NAME_MAX_LENGTH}. Use a shorter domain."
        )
