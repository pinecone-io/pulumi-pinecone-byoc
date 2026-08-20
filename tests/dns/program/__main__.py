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

# Route53's default SOA makes a resolver cache a miss for 15 minutes, and this zone
# is asked about a cell that does not exist yet on every run
aws.route53.Record(
    "cell-parent-soa",
    zone_id=zone.id,
    name=fqdn,
    type="SOA",
    ttl=60,
    records=[
        zone.name_servers[0].apply(
            lambda ns: f"{ns}. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 60"
        )
    ],
    allow_overwrite=True,
)

pulumi.export("zone_id", zone.id)
pulumi.export("fqdn", fqdn)
pulumi.export("name_servers", zone.name_servers)
