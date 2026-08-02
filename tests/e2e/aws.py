import logging

import boto3

ELB_TAG = "kubernetes.io/role/elb"
INTERNAL_ELB_TAG = "kubernetes.io/role/internal-elb"


def tags(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def list_clusters(region):
    eks = boto3.client("eks", region_name=region)
    return {
        name for page in eks.get_paginator("list_clusters").paginate() for name in page["clusters"]
    }


def find_cluster_for_vpc(vpc_id, region, baseline=None):
    """Locate this run's EKS cluster.

    With a vpc_id (BYO-VPC) the cluster is matched by the VPC it sits in. For a
    vanilla deploy the VPC is created by the module and unknown up front, so the
    cluster is whichever one did not exist when the run started - which needs the
    baseline, or every cluster in the account looks like a candidate.
    """
    if vpc_id is None and baseline is None:
        logging.info("[pinetools] no vpc and no baseline: refusing to guess which cluster is ours")
        return None, None

    already_there = baseline or set()
    eks = boto3.client("eks", region_name=region)
    for name in sorted(list_clusters(region)):
        if vpc_id is None and name in already_there:
            continue
        try:
            cluster = eks.describe_cluster(name=name)["cluster"]
        except Exception:  # noqa: BLE001 - a prior run's cluster can vanish mid-listing
            continue
        if vpc_id is None or cluster.get("resourcesVpcConfig", {}).get("vpcId") == vpc_id:
            return name, cluster.get("status")
    return None, None
