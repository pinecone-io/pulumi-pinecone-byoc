"""Shared naming conventions and constants for BYOC clusters."""

import re

import pulumi

from .providers import Environment

ORG_NAME_MAX_LENGTH = 16

# CNAME records created in both DNS and NLB components across all clouds
DNS_CNAMES = ["*.svc", "metrics", "prometheus"]

# the CN of an X.509 certificate, which ACM enforces on the first domain name
CERTIFICATE_NAME_MAX_LENGTH = 64


def cell_name(environment: Environment) -> pulumi.Output[str]:
    """Derive cell name from environment: e.g. pinecone-byoc-ef7a"""

    def sanitize(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())[:ORG_NAME_MAX_LENGTH]

    return pulumi.Output.all(environment.org_name, environment.env_name).apply(
        lambda args: f"{sanitize(args[0])}-byoc-{args[1].split('.')[0][-4:]}"
    )


def refuse_a_domain_no_certificate_can_cover(domain: str, region: str, global_env: str) -> None:
    """ACM will not issue a certificate whose first domain name exceeds 64 characters.

    The module's private cert asks for *.svc.private.{cell}.byoc.{domain}, and the
    cell's name is not known until the control plane assigns it - so this asks what
    the longest one for this region would be. Getting it wrong costs an hour of
    cluster and then a failure inside ACM that names none of this.
    """
    prefix = "" if global_env == "prod" else f"{global_env}-"
    longest_cell = f"{prefix}aws-{region}-ab12.byoc"
    budget = CERTIFICATE_NAME_MAX_LENGTH - len(f"*.svc.private.{longest_cell}.")
    if len(domain) > budget:
        raise ValueError(
            f"{domain} is {len(domain)} characters, and at most {budget} fit here: a "
            f"cell in {region} is named {longest_cell}, and the certificate that covers "
            f"it may not exceed {CERTIFICATE_NAME_MAX_LENGTH}. Use a shorter domain."
        )
