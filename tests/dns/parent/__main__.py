"""Run by hand, once.

byodns-pinecone.click is registered in byoc-dev, whose apex zone the registrar points
at. This makes the zone the e2e hangs its cells off, in byoc-ci, and nothing else -
the NS record that delegates it is in another account and stays a manual step:

pulumi -C tests/dns/parent stack init pinecone/byoc-ci-click-zone
pulumi -C tests/dns/parent config set aws:region us-east-2 --stack pinecone/byoc-ci-click-zone
pulumi -C tests/dns/parent config set aws:profile byoc-ci --stack pinecone/byoc-ci-click-zone
pulumi -C tests/dns/parent config set domain c.byodns-pinecone.click --stack pinecone/byoc-ci-click-zone
pulumi -C tests/dns/parent up --stack pinecone/byoc-ci-click-zone

then, with the four nameservers the delegation output prints, in byoc-dev:

aws route53 change-resource-record-sets --hosted-zone-id Z076019239I8D42NNON3E \
  --profile byoc-dev --region us-east-1 --change-batch '{"Changes": [{"Action": "UPSERT",
  "ResourceRecordSet": {"Name": "c.byodns-pinecone.click.", "Type": "NS", "TTL": 300,
  "ResourceRecords": [{"Value": "<ns1>."}, ...]}}]}'

That record is nobody's resource, so destroying this stack would leave the apex
pointing at nameservers that answer for nobody. Delete it by hand first, with the same
call and "DELETE".
"""

import pulumi
import pulumi_aws as aws

from pulumi_pinecone_byoc.common.naming import refuse_a_domain_no_certificate_can_cover

config = pulumi.Config()
domain = config.require("domain")
region = config.get("region") or "us-east-2"
global_env = config.get("global-env") or "ci"

refuse_a_domain_no_certificate_can_cover(domain, region, global_env)

zone = aws.route53.Zone(
    "e2e-parent",
    name=domain,
    tags={"pinecone:managed-by": "pulumi", "pinecone:purpose": "byodns-e2e"},
    opts=pulumi.ResourceOptions(protect=True),
)

aws.route53.Record(
    "negative-cache",
    zone_id=zone.id,
    name=domain,
    type="SOA",
    ttl=60,
    records=[
        zone.name_servers[0].apply(
            lambda ns: f"{ns}. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 60"
        )
    ],
    allow_overwrite=True,
)

pulumi.export("domain", domain)
pulumi.export("name_servers", zone.name_servers)
pulumi.export(
    "delegation",
    zone.name_servers.apply(lambda servers: [f"{domain}.  NS  {server}." for server in servers]),
)
