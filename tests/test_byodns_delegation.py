import pulumi
import pytest

from pulumi_pinecone_byoc.aws.dns import DNS
from pulumi_pinecone_byoc.common.naming import (
    refuse_a_domain_no_certificate_can_cover,
    refuse_a_domain_only_aws_can_be_delegated,
)

DYNAMIC = "pulumi-python:dynamic:Resource"
NS_RECORD = "aws:route53/record:Record"


class Engine(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append((args.typ, args.name, args.inputs))
        outputs = dict(args.inputs)
        if args.typ == "aws:acm/certificate:Certificate":
            outputs["domainValidationOptions"] = [
                {
                    "resourceRecordName": f"_validation-{i}.{args.inputs.get('domainName', '')}",
                    "resourceRecordType": "CNAME",
                    "resourceRecordValue": f"_value-{i}.acm-validations.aws",
                }
                for i in range(4)
            ]
        return f"{args.name}-id", outputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        return {}

    def ns_records(self, zone_id):
        return [
            inputs
            for typ, _name, inputs in self.resources
            if typ == NS_RECORD and inputs.get("type") == "NS" and inputs.get("zoneId") == zone_id
        ]

    def delegations(self):
        return [
            name
            for typ, name, inputs in self.resources
            if typ == DYNAMIC and "cpgw_api_key" in inputs
        ]


def dns(parent_zone_id=None, domain="pinecone.io"):
    engine = Engine()
    pulumi.runtime.set_mocks(engine, preview=False)
    component = DNS(
        "pc-dns",
        subdomain="aws-us-east-2-ab12",
        fqdn=f"aws-us-east-2-ab12.byoc.{domain}",
        api_url="https://api-staging.pinecone.io",
        cpgw_api_key="not-a-key",
        parent_zone_id=parent_zone_id,
        pinecone_hosted=domain == "pinecone.io",
        delegation_wait_seconds=0,
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


@pytest.mark.parametrize("cloud", ["gcp", "azure"])
def test_only_aws_takes_a_domain_pinecone_does_not_host(cloud):
    refuse_a_domain_only_aws_can_be_delegated("pinecone.io", cloud)
    with pytest.raises(ValueError, match="resolves under pinecone.io"):
        refuse_a_domain_only_aws_can_be_delegated("corp.example.com", cloud)


def private_cert(engine):
    (cert,) = [
        inputs
        for typ, name, inputs in engine.resources
        if typ == "aws:acm/certificate:Certificate" and "private-cert" in name
    ]
    return cert


@pytest.mark.parametrize(
    ("domain", "first_name"),
    [
        ("pinecone.io", "*.svc.private.aws-us-east-2-ab12.byoc.pinecone.io"),
        ("corp.example.com", "private.aws-us-east-2-ab12.byoc.corp.example.com"),
    ],
    ids=[
        "our_own_domain_keeps_the_certificate_it_has",
        "a_customer_domain_leads_with_a_short_name",
    ],
)
@pulumi.runtime.test
def test_what_the_private_certificate_is_named(domain, first_name):
    engine, component = dns(domain=domain)

    def check(_arns):
        cert = private_cert(engine)
        assert cert["domainName"] == first_name
        assert len(cert["domainName"]) <= 64
        covered = {cert["domainName"], *cert["subjectAlternativeNames"]}
        for label in ("*.svc", "metrics", "prometheus"):
            assert f"{label}.private.aws-us-east-2-ab12.byoc.{domain}" in covered

    return pulumi.Output.all(component.certificate_arn, component.private_certificate_arn).apply(
        check
    )


@pytest.mark.parametrize(
    ("domain", "records"),
    [("pinecone.io", 3), ("corp.example.com", 4)],
    ids=["three_names_three_records", "a_fourth_name_needs_a_fourth_record"],
)
@pulumi.runtime.test
def test_every_private_name_gets_a_validation_record(domain, records):
    engine, component = dns(domain=domain)

    def check(_arns):
        validations = [
            name
            for typ, name, _inputs in engine.resources
            if typ == "aws:route53/record:Record" and "private-cert-validation" in name
        ]
        assert len(validations) == records

    return pulumi.Output.all(component.certificate_arn, component.private_certificate_arn).apply(
        check
    )
