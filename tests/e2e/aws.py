import logging
import re

import boto3

ELB_TAG = "kubernetes.io/role/elb"
INTERNAL_ELB_TAG = "kubernetes.io/role/internal-elb"
CLUSTER_ADMIN_POLICY = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
KUBECONFIG_CLUSTER = re.compile(r"--name\s+(\S+)")


def tags(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def cluster_from_outputs(outputs):
    match = KUBECONFIG_CLUSTER.search(outputs.get("update_kubeconfig_command", ""))
    return match.group(1) if match else None


def caller_principal_arn():
    """The IAM role behind the current session, as EKS wants it.

    get_caller_identity reports an sts assumed-role ARN, which an access entry
    rejects; the role's own ARN carries the path (aws-reserved/... for SSO)
    that EKS validates against IAM.
    """
    arn = boto3.client("sts").get_caller_identity()["Arn"]
    if ":assumed-role/" not in arn:
        return arn
    role_name = arn.split(":assumed-role/")[1].split("/")[0]
    return boto3.client("iam").get_role(RoleName=role_name)["Role"]["Arn"]


def grant_cluster_admin(cluster, region):
    principal = caller_principal_arn()
    eks = boto3.client("eks", region_name=region)
    try:
        eks.create_access_entry(clusterName=cluster, principalArn=principal, type="STANDARD")
    except eks.exceptions.ResourceInUseException:
        logging.info("[eks] %s already has an access entry on %s", principal, cluster)
    eks.associate_access_policy(
        clusterName=cluster,
        principalArn=principal,
        policyArn=CLUSTER_ADMIN_POLICY,
        accessScope={"type": "cluster"},
    )
    return principal


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


def vpc_of_cluster(cluster, region):
    eks = boto3.client("eks", region_name=region)
    described = eks.describe_cluster(name=cluster)["cluster"]
    return described.get("resourcesVpcConfig", {}).get("vpcId")


def load_balancers_in(vpc_id, region):
    elb = boto3.client("elbv2", region_name=region)
    return [
        (lb["LoadBalancerName"], lb.get("Scheme"), lb.get("Type"))
        for page in elb.get_paginator("describe_load_balancers").paginate()
        for lb in page["LoadBalancers"]
        if lb.get("VpcId") == vpc_id
    ]
