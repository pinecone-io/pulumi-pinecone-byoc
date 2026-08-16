import boto3
import botocore.exceptions
import pulumi

from config.aws import AWSConfig

from . import vpc_subnet

# no DryRun parameter, and everything after the network has no dry run at all
NO_DRY_RUN = (
    "ec2:AssociateVpcCidrBlock",
    "ec2:ModifySubnetAttribute",
    "ec2:CreateRoute",
)

# a dry run is only truthful about a call we can make well-formed, and this one names
# a subnet before we have made any. AWS decides authorization before it looks the
# resource up, so a scoped policy still answers; a region that looks first says
# NotFound, which is read as inconclusive
UNMADE_SUBNET = "subnet-" + "0" * 17


def _verdict(name: str, call, **kwargs) -> str | None:
    try:
        call(DryRun=True, **kwargs)
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "UnauthorizedOperation":
            return name
        if code != "DryRunOperation":
            pulumi.log.info(f"{name}: {code}, so permission for it is unknown")
    except Exception as exc:  # noqa: BLE001 - an unanswered check is not a failure
        pulumi.log.info(f"{name}: not checked ({exc})")
    return None


def refused(config: AWSConfig, vpc_id: str, route_table_ids: dict[str, str]) -> list[str]:
    ec2 = boto3.client("ec2", region_name=config.region)
    az = config.availability_zones[0]

    probes = [
        (
            "ec2:CreateSubnet",
            ec2.create_subnet,
            {
                "VpcId": vpc_id,
                "CidrBlock": str(vpc_subnet.cidr(config.vpc_cidr, 0, is_public=False)),
                "AvailabilityZone": az,
                # a policy conditioned on tags refuses a probe that does not carry them
                "TagSpecifications": [
                    {
                        "ResourceType": "subnet",
                        "Tags": [
                            {"Key": key, "Value": value}
                            for key, value in config.tags(
                                Name=f"{config.resource_prefix}-private-{az}",
                                **{"kubernetes.io/role/internal-elb": "1"},
                            ).items()
                        ],
                    }
                ],
            },
        ),
        (
            "ec2:CreateSecurityGroup",
            ec2.create_security_group,
            {
                "VpcId": vpc_id,
                "GroupName": f"{config.resource_prefix}-lb-backend-sg",
                "Description": "Shared backend security group for load balancers",
            },
        ),
    ]

    if config.public_access:
        probes.append(("ec2:CreateRouteTable", ec2.create_route_table, {"VpcId": vpc_id}))

    for table_id in sorted(set(route_table_ids.values())):
        probes.append(
            (
                f"ec2:AssociateRouteTable on {table_id}",
                ec2.associate_route_table,
                {"RouteTableId": table_id, "SubnetId": UNMADE_SUBNET},
            )
        )

    return [name for name, call, kwargs in probes if _verdict(name, call, **kwargs)]


def explain(config: AWSConfig, vpc_id: str, denied: list[str]) -> str:
    return (
        f"The AWS credential is not allowed to build in {vpc_id}: "
        f"{', '.join(denied)} refused. Grant it these on that VPC, or leave "
        "existing_vpc_id unset to deploy into a VPC of our own. "
        "A policy conditioned on tags this deploy does not carry reads as a refusal "
        f"here too, and the tags asked for were {sorted(config.tags())}. "
        f"Neither refused nor allowed, having no dry run: {', '.join(NO_DRY_RUN)}, "
        "along with the EKS, load balancer and RDS calls that come after the network."
    )


def warn(config: AWSConfig, vpc_id: str, route_table_ids: dict[str, str]) -> None:
    denied = refused(config, vpc_id, route_table_ids)
    if denied:
        pulumi.log.warn(explain(config, vpc_id, denied))
