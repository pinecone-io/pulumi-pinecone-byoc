"""Run by hand, once.

    pulumi -C tests/dns/parent stack init pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent config set aws:region us-east-2 --stack pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent config set aws:profile byoc-ci --stack pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent config set domain dns.byoc.pinecone.io --stack pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent config set parent-zone-id Z07162811W71EK85S6OVS --stack pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent config set delegator-stack pinecone/secrets-shared/prod-aws --stack pinecone/byoc-ci-byodns-zone
    pulumi -C tests/dns/parent up --stack pinecone/byoc-ci-byodns-zone

Leave the last two unset and it makes the zone only, exporting the record for
whoever can reach the parent to add by hand.
"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config()
domain = config.require("domain")
parent_zone_id = config.get("parent-zone-id")
delegator_stack = config.get("delegator-stack")

BUDGET = 64 - len("*.svc.private.ci-aws-us-east-2-b3a7.byoc.")
if len(domain) > BUDGET:
    raise ValueError(
        f"{domain} is {len(domain)} characters; at most {BUDGET} leaves room for the "
        "private certificate's first domain name, which ACM caps at 64"
    )

zone = aws.route53.Zone(
    "e2e-parent",
    name=domain,
    tags={"pinecone:managed-by": "pulumi", "pinecone:purpose": "byodns-e2e"},
    opts=pulumi.ResourceOptions(protect=True),
)

if parent_zone_id and delegator_stack:
    # the parent is in an account no role here can reach; this is the identity the
    # satellites use to delegate into it, and pulumi keeps its key out of a terminal
    shared = pulumi.StackReference(delegator_stack)
    aws.route53.Record(
        "delegation",
        zone_id=parent_zone_id,
        name=domain,
        type="NS",
        ttl=300,
        records=zone.name_servers,
        allow_overwrite=True,
        opts=pulumi.ResourceOptions(
            provider=aws.Provider(
                "delegator",
                region="us-east-1",
                access_key=shared.get_output("privileged_dns_delegator_access_key_id"),
                secret_key=shared.get_output("privileged_dns_delegator_access_key_secret"),
            )
        ),
    )

pulumi.export("domain", domain)
pulumi.export("name_servers", zone.name_servers)
pulumi.export(
    "delegation",
    zone.name_servers.apply(lambda servers: [f"{domain}.  NS  {server}." for server in servers]),
)
