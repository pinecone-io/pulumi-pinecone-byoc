"""The half of BYO-DNS a customer owns, stood up so a test can exercise it.

A cell's zone has to be reachable from the root, and the only way to know that it
is, is for something above it to say so. The registered domain is set up by hand
and outlives every run, so this is where the chain starts: the zone we host for
them, and the NS record in theirs that points at it.

Their record is written here rather than by the module, because that is the shape
of the thing - a delegation from a zone we were never given is theirs to make, and
a test that let the module write it would be exercising the case where they gave
us their zone instead.
"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config()
domain = config.require("domain")
parent_zone_id = config.require("parent-zone-id")

tags = {"pinecone:managed-by": "pulumi", "pinecone:purpose": "byodns-e2e"}
fqdn = f"byoc.{domain}"

zone = aws.route53.Zone(
    "cell-parent",
    name=fqdn,
    force_destroy=True,
    tags={**tags, "Name": fqdn},
)

aws.route53.Record(
    "cell-parent-delegation",
    zone_id=parent_zone_id,
    name=fqdn,
    type="NS",
    records=zone.name_servers,
    ttl=300,
    allow_overwrite=True,
)

pulumi.export("zone_id", zone.id)
pulumi.export("fqdn", fqdn)
pulumi.export("name_servers", zone.name_servers)
