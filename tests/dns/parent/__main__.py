"""The zone that stands in for a customer's own, and outlives every run.

Everything below this is made and destroyed with a run: the zone we host for them,
the cell's zone, its records and certificates. This one is not. What makes it
resolve is an NS record in pinecone.io, and pinecone.io is not in this account -
so that record is written by hand, once, and stays right for exactly as long as
this zone keeps the nameservers it was given. Destroying it would leave a record
in a zone we do not own pointing at nameservers that answer for nobody, which is
the shape of a subdomain takeover, so the zone is protected.

    pulumi -C tests/dns/parent stack init pinecone/byodns-parent
    pulumi -C tests/dns/parent config set aws:region us-east-2 --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent config set aws:profile byoc-ci --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent config set domain byodns-ci.pinecone.io --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent up --stack pinecone/byodns-parent
    pulumi -C tests/dns/parent stack output delegation --stack pinecone/byodns-parent

The last of those prints the record to add. Until it is added the zone answers to
nobody, and the byodns shapes refuse to deploy rather than find out an hour later
inside ACM.
"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config()
domain = config.require("domain")

# what the module's private certificate spends before this name starts, and ACM
# will not issue a certificate whose first domain name is longer than 64
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
