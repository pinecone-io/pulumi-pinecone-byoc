"""Remove delegations from the parent zone that no nameserver answers for.

A cell's NS record is written when the cell is created and removed when it is
destroyed. A destroy that failed and was unstuck by editing state never calls
the remove, and the record then points at nameservers that answer for nobody.
On some managed DNS services anyone can create a zone that lands on the same
nameserver set and answer for the name instead: the record is a subdomain
takeover waiting to happen.

A delegation is dangling when every nameserver it lists refuses the zone. One
authoritative answer keeps it. One nameserver that cannot be reached, or that
answers something other than a refusal, leaves the record alone and fails the
run: a network blip must not be able to delete a live cell.
"""

import argparse
import logging
import os
import sys
from collections.abc import Callable

import boto3
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.resolver

ALIVE = "alive"
DANGLING = "dangling"
UNSURE = "unsure"

# what a nameserver says when asked for a zone it does not host
REFUSALS = {dns.rcode.REFUSED, dns.rcode.NXDOMAIN}


def ask(zone, nameserver, timeout=5.0):
    """One nameserver's word on one zone: does it answer for it?"""
    query = dns.message.make_query(zone, dns.rdatatype.SOA)
    query.flags &= ~dns.flags.RD
    try:
        addresses = [str(record) for record in dns.resolver.resolve(nameserver, "A")]
    except Exception:
        return UNSURE
    for address in addresses:
        try:
            answer = dns.query.udp(query, address, timeout=timeout)
        except Exception:
            continue
        if answer.rcode() == dns.rcode.NOERROR and answer.flags & dns.flags.AA:
            return ALIVE
        if answer.rcode() in REFUSALS:
            return DANGLING
        return UNSURE
    return UNSURE


def verdict(zone, nameservers, ask=ask):
    words = {ask(zone, nameserver) for nameserver in nameservers}
    if ALIVE in words:
        return ALIVE
    if words == {DANGLING}:
        return DANGLING
    return UNSURE


def delegations(r53, zone_id):
    apex = r53.get_hosted_zone(Id=zone_id)["HostedZone"]["Name"]
    return [
        record
        for page in r53.get_paginator("list_resource_record_sets").paginate(HostedZoneId=zone_id)
        for record in page["ResourceRecordSets"]
        if record["Type"] == "NS" and record["Name"] != apex
    ]


def classify(records, ask=ask):
    return {
        record["Name"]: verdict(
            record["Name"].rstrip("."),
            [r["Value"].rstrip(".") for r in record["ResourceRecords"]],
            ask,
        )
        for record in records
    }


def remove(r53, zone_id, records):
    for record in records:
        r53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={"Changes": [{"Action": "DELETE", "ResourceRecordSet": record}]},
        )


def sweep(r53, zone_id, limit, dry_run, ask: Callable = ask):
    """Classify every delegation and remove the dangling ones.

    Returns the exit status: 0 when every record was accounted for, 1 when a
    human has to look - a nameserver that could not be reached, or more
    removals than one run is trusted with.
    """
    records = delegations(r53, zone_id)
    verdicts = classify(records, ask)
    for name, word in sorted(verdicts.items()):
        logging.info("%-9s %s", word, name)

    dangling = [record for record in records if verdicts[record["Name"]] == DANGLING]
    unsure = [name for name, word in verdicts.items() if word == UNSURE]

    if len(dangling) > limit:
        logging.error(
            "%d delegations look dangling and this run may remove %d; removing none. "
            "Look at them, then rerun with a higher --limit.",
            len(dangling),
            limit,
        )
        summary(verdicts, dangling, "left in place, over the limit")
        return 1
    if dry_run:
        summary(verdicts, dangling, "would remove")
    else:
        remove(r53, zone_id, dangling)
        logging.info("removed %d", len(dangling))
        summary(verdicts, dangling, "removed")
    if unsure:
        logging.error("could not decide for %s", ", ".join(sorted(unsure)))
        return 1
    return 0


def summary(verdicts, dangling, outcome):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        f"### byoc.pinecone.io delegations: {len(verdicts)} checked, {len(dangling)} {outcome}",
        "",
        "| verdict | delegation |",
        "|---|---|",
        *(f"| {word} | `{name}` |" for name, word in sorted(verdicts.items())),
        "",
    ]
    with open(path, "a") as out:
        out.write("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--zone-id", required=True, help="the hosted zone holding the delegations")
    parser.add_argument("--dry-run", action="store_true", help="report, remove nothing")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="refuse to remove more than this many in one run (default 5)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return sweep(boto3.client("route53"), args.zone_id, args.limit, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
