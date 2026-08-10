import pulumi
import pulumi_aws as aws

DEFAULT_ROUTE = "0.0.0.0/0"

# what a default route can leave by and still reach the registry. a peering
# connection cannot: AWS routes nothing transitively, so the peer's NAT and gateway
# are unreachable and the packets are dropped. an internet gateway can, but that is
# how their public subnets get out, not their private ones
EGRESS_TARGETS = (
    "nat_gateway_id",
    "transit_gateway_id",
    "vpc_endpoint_id",  # a gateway load balancer endpoint, fronting their firewall
    "network_interface_id",  # a NAT instance
    "instance_id",
    "core_network_arn",
)


def egress_target(route) -> str | None:
    for field in EGRESS_TARGETS:
        target = getattr(route, field, None)
        if target:
            return target
    # a virtual private gateway: they egress through on-prem over VPN or Direct Connect
    gateway = getattr(route, "gateway_id", None) or ""
    if gateway.startswith("vgw-"):
        return gateway
    return None


# get_route_table reports no state for a route, so a default route whose target was
# deleted reads the same as a live one. the wizard's preflight has boto3 and checks it
def _egress_of(table) -> str | None:
    for route in table.routes:
        if route.cidr_block != DEFAULT_ROUTE:
            continue
        return egress_target(route)
    return None


def _is_main(table) -> bool:
    return any(association.main for association in table.associations)


def detect(vpc_id: str, azs: list[str]) -> dict[str, str]:
    ids = aws.ec2.get_route_tables(vpc_id=vpc_id).ids
    tables = [aws.ec2.get_route_table(route_table_id=table_id) for table_id in ids]

    detected: dict[str, str] = {}
    for az in azs:
        theirs = set(
            aws.ec2.get_subnets(
                filters=[
                    {"name": "vpc-id", "values": [vpc_id]},
                    {"name": "availability-zone", "values": [az]},
                ]
            ).ids
        )
        candidates = {}
        for table in tables:
            associated = {a.subnet_id for a in table.associations if a.subnet_id}
            if not associated & theirs:
                continue
            egress = _egress_of(table)
            if egress:
                candidates[table.route_table_id] = egress

        if not candidates:
            pulumi.log.info(
                f"{az}: no route table of theirs egresses without an internet gateway; "
                "the subnet will inherit the VPC main route table"
            )
            continue
        if len(candidates) > 1:
            raise ValueError(
                f"{vpc_id} has more than one route table egressing from {az} "
                f"({', '.join(f'{t} via {e}' for t, e in sorted(candidates.items()))}). "
                "Name the one to use per availability zone in existing_route_table_ids."
            )

        table_id, egress = next(iter(candidates.items()))
        if _is_main(next(t for t in tables if t.route_table_id == table_id)):
            pulumi.log.info(f"{az}: egresses via {egress} on the main route table, inherited")
            continue
        pulumi.log.info(f"{az}: {table_id}, egressing via {egress}")
        detected[az] = table_id

    return detected
