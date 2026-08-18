"""Run by hand, once. Nothing destroys this zone: the record that points at it
lives in a zone we do not own.

    pulumi -C tests/dns/parent stack init pinecone/byodns-parent
    pulumi -C tests/dns/parent config set aws:region us-east-2 --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent config set aws:profile byoc-ci --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent config set domain dns.byoc.pinecone.io --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent up --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent stack output delegation --stack pinecone/byodns-parent
"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config()
domain = config.require("domain")

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

pulumi.export("domain", domain)
pulumi.export("name_servers", zone.name_servers)
pulumi.export(
    "delegation",
    zone.name_servers.apply(
        lambda servers: "\n".join(
            [f"Add these in the zone that serves {domain.split('.', 1)[1]}:", ""]
            + [f"    {domain}.  NS  {server}." for server in servers]
        )
    ),
)
