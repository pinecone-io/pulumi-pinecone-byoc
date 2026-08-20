import logging
import re

import boto3
import requests

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


def parent_zone(domain):
    r53 = boto3.client("route53")
    listed = r53.list_hosted_zones_by_name(DNSName=domain, MaxItems="1")
    for zone in listed["HostedZones"]:
        if zone["Name"].rstrip(".") == domain and not zone["Config"]["PrivateZone"]:
            zone_id = zone["Id"].removeprefix("/hostedzone/")
            return zone_id, r53.get_hosted_zone(Id=zone_id)["DelegationSet"]["NameServers"]
    raise AssertionError(
        f"no public hosted zone for {domain} in this account. Deploy the byodns-aws stack "
        "of pinecone-platform/pulumi/dns-zones, and name the zone it makes as "
        "e2e_parent_domain in pytest.ini"
    )


def assert_delegated(domain, nameservers):
    answer = requests.get(
        "https://dns.google/resolve", params={"name": domain, "type": "NS"}, timeout=15
    ).json()
    served_by = {
        record["data"].rstrip(".").lower()
        for record in answer.get("Answer", [])
        if record.get("type") == 2  # NS
    }
    expected = {server.rstrip(".").lower() for server in nameservers}
    if expected <= served_by:
        return

    records = "\n".join(f"      {domain}.  NS  {server}." for server in sorted(expected))
    raise AssertionError(
        f"{domain} is not delegated: a public resolver says "
        f"{sorted(served_by) or 'nothing'} serves it, not {sorted(expected)}.\n"
        f"    Add this in the zone that serves {domain.split('.', 1)[1]}:\n{records}"
    )


def private_dns_verification_state(fqdn, region):
    wanted = f"*.private.{fqdn}"
    ec2 = boto3.client("ec2", region_name=region)
    for page in ec2.get_paginator("describe_vpc_endpoint_service_configurations").paginate():
        for service in page["ServiceConfigurations"]:
            if service.get("PrivateDnsName") == wanted:
                return service.get("PrivateDnsNameConfiguration", {}).get("State", "")
    raise AssertionError(f"no endpoint service in {region} claims {wanted}")


def cell_zone(project_dir_state):
    for resource in project_dir_state["deployment"]["resources"]:
        if resource["type"] == "aws:route53/zone:Zone":
            outputs = resource["outputs"]
            return outputs["name"], outputs["nameServers"]
    raise AssertionError("the deploy made no hosted zone for the cell")


def delegate(zone_id, fqdn, nameservers, action="UPSERT"):
    boto3.client("route53").change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={
            "Changes": [
                {
                    "Action": action,
                    "ResourceRecordSet": {
                        "Name": fqdn,
                        "Type": "NS",
                        "TTL": 300,
                        "ResourceRecords": [{"Value": server} for server in nameservers],
                    },
                }
            ]
        },
    )
