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
