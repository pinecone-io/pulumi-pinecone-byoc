import pulumi
import pytest

from pulumi_pinecone_byoc.aws.dns import DNS
from pulumi_pinecone_byoc.common.naming import refuse_a_domain_no_certificate_can_cover

DELEGATION = "pulumi-python:dynamic:Resource"
NS_RECORD = "aws:route53/record:Record"


class Engine(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append((args.typ, args.name, args.inputs))
        return f"{args.name}-id", args.inputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        return {}

    def ns_records(self, zone_id):
        return [
            inputs
            for typ, _name, inputs in self.resources
            if typ == NS_RECORD and inputs.get("type") == "NS" and inputs.get("zoneId") == zone_id
        ]

    def delegations(self):
        return [name for typ, name, _inputs in self.resources if typ == DELEGATION]


def dns(parent_zone_id=None, domain="pinecone.io"):
    engine = Engine()
    pulumi.runtime.set_mocks(engine, preview=True)
    component = DNS(
        "pc-dns",
        subdomain="aws-us-east-2-ab12",
        fqdn=f"aws-us-east-2-ab12.byoc.{domain}",
        api_url="https://api-staging.pinecone.io",
        cpgw_api_key="not-a-key",
        parent_zone_id=parent_zone_id,
        pinecone_hosted=domain == "pinecone.io",
    )
    return engine, component


@pytest.mark.parametrize(
    ("parent_zone_id", "domain", "asks_control_plane", "writes_ns_into"),
    [
        (None, "pinecone.io", True, None),
        ("Z0PARENT", "corp.example.com", False, "Z0PARENT"),
        (None, "corp.example.com", False, None),
        ("Z0PARENT", "pinecone.io", False, "Z0PARENT"),
    ],
    ids=[
        "our_zone_is_delegated_by_the_control_plane",
        "their_zone_in_reach_is_delegated_with_the_aws_provider",
        "their_zone_out_of_reach_is_delegated_by_them",
        "a_reachable_parent_wins_even_on_our_own_domain",
    ],
)
@pulumi.runtime.test
def test_who_writes_the_delegation(parent_zone_id, domain, asks_control_plane, writes_ns_into):
    engine, component = dns(parent_zone_id=parent_zone_id, domain=domain)

    def check(_arns):
        assert bool(engine.delegations()) is asks_control_plane
        if writes_ns_into:
            (record,) = engine.ns_records(writes_ns_into)
            assert record["name"] == f"aws-us-east-2-ab12.byoc.{domain}"
        else:
            assert engine.ns_records("Z0PARENT") == []

    return pulumi.Output.all(component.certificate_arn, component.private_certificate_arn).apply(
        check
    )


@pytest.mark.parametrize(
    ("domain", "region", "global_env", "fits"),
    [
        ("corp.example.com", "us-east-1", "prod", True),
        ("pinecone.acme.com", "ap-southeast-1", "prod", True),
        ("dns.byoc.pinecone.io", "us-east-2", "ci", True),
        ("pinecone.some-long-company-name.com", "us-east-1", "prod", False),
        ("pinecone.acme.com", "us-east-2", "staging", True),
    ],
)
def test_a_domain_no_certificate_could_cover_is_refused(domain, region, global_env, fits):
    if fits:
        refuse_a_domain_no_certificate_can_cover(domain, region, global_env)
        return
    with pytest.raises(ValueError, match="characters"):
        refuse_a_domain_no_certificate_can_cover(domain, region, global_env)
