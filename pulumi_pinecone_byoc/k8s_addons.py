from typing import Optional
import json

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs

from config import Config
from .eks import EKS

AWS_LOAD_BALANCER_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["iam:CreateServiceLinkedRole"],
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"
                }
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeAccountAttributes",
                "ec2:DescribeAddresses",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeVpcs",
                "ec2:DescribeVpcPeeringConnections",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInstances",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeTags",
                "ec2:GetCoipPoolUsage",
                "ec2:DescribeCoipPools",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeLoadBalancerAttributes",
                "elasticloadbalancing:DescribeListeners",
                "elasticloadbalancing:DescribeListenerAttributes",
                "elasticloadbalancing:DescribeListenerCertificates",
                "elasticloadbalancing:DescribeSSLPolicies",
                "elasticloadbalancing:DescribeRules",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:DescribeTargetGroupAttributes",
                "elasticloadbalancing:DescribeTargetHealth",
                "elasticloadbalancing:DescribeTags",
                "elasticloadbalancing:DescribeTrustStores",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "cognito-idp:DescribeUserPoolClient",
                "acm:ListCertificates",
                "acm:DescribeCertificate",
                "iam:ListServerCertificates",
                "iam:GetServerCertificate",
                "waf-regional:GetWebACL",
                "waf-regional:GetWebACLForResource",
                "waf-regional:AssociateWebACL",
                "waf-regional:DisassociateWebACL",
                "wafv2:GetWebACL",
                "wafv2:GetWebACLForResource",
                "wafv2:AssociateWebACL",
                "wafv2:DisassociateWebACL",
                "shield:GetSubscriptionState",
                "shield:DescribeProtection",
                "shield:CreateProtection",
                "shield:DeleteProtection",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
            ],
            "Resource": "*",
        },
        {"Effect": "Allow", "Action": ["ec2:CreateSecurityGroup"], "Resource": "*"},
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateTags"],
            "Resource": "arn:aws:ec2:*:*:security-group/*",
            "Condition": {
                "StringEquals": {"ec2:CreateAction": "CreateSecurityGroup"},
                "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
            },
        },
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateTags", "ec2:DeleteTags"],
            "Resource": "arn:aws:ec2:*:*:security-group/*",
            "Condition": {
                "Null": {
                    "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                    "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                }
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:DeleteSecurityGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:CreateLoadBalancer",
                "elasticloadbalancing:CreateTargetGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:CreateListener",
                "elasticloadbalancing:DeleteListener",
                "elasticloadbalancing:CreateRule",
                "elasticloadbalancing:DeleteRule",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:RemoveTags",
            ],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
            ],
            "Condition": {
                "Null": {
                    "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                    "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                }
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:RemoveTags",
            ],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:listener/net/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener/app/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener-rule/net/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener-rule/app/*/*/*",
            ],
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:ModifyLoadBalancerAttributes",
                "elasticloadbalancing:SetIpAddressType",
                "elasticloadbalancing:SetSecurityGroups",
                "elasticloadbalancing:SetSubnets",
                "elasticloadbalancing:DeleteLoadBalancer",
                "elasticloadbalancing:ModifyTargetGroup",
                "elasticloadbalancing:ModifyTargetGroupAttributes",
                "elasticloadbalancing:DeleteTargetGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": ["elasticloadbalancing:AddTags"],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
            ],
            "Condition": {
                "StringEquals": {
                    "elasticloadbalancing:CreateAction": [
                        "CreateTargetGroup",
                        "CreateLoadBalancer",
                    ]
                },
                "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:RegisterTargets",
                "elasticloadbalancing:DeregisterTargets",
            ],
            "Resource": "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:SetWebAcl",
                "elasticloadbalancing:ModifyListener",
                "elasticloadbalancing:AddListenerCertificates",
                "elasticloadbalancing:RemoveListenerCertificates",
                "elasticloadbalancing:ModifyRule",
                "elasticloadbalancing:SetRulePriorities",
                "elasticloadbalancing:ModifyListenerAttributes",
            ],
            "Resource": "*",
        },
    ],
}


class K8sAddons(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: Config,
        eks: EKS,
        vpc_id: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:K8sAddons", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        # resource_suffix for unique AWS resource names (last 4 chars of cell_name)
        self._resource_suffix = self._cell_name.apply(lambda cn: cn[-4:])
        child_opts = pulumi.ResourceOptions(parent=self)

        self.gloo_namespace = self._create_gloo_namespace(
            name, eks.provider, child_opts
        )

        self.alb_controller_role = self._create_alb_controller_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        self.alb_controller = self._create_alb_controller(
            name,
            eks.provider,
            eks.cluster_name,
            vpc_id,
            self.alb_controller_role.arn,
            pulumi.ResourceOptions(parent=self, depends_on=[self.alb_controller_role]),
        )

        self.cluster_autoscaler_role = self._create_cluster_autoscaler_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        self.cluster_autoscaler = self._create_cluster_autoscaler(
            name,
            eks.provider,
            eks.cluster_name,
            self.cluster_autoscaler_role.arn,
            pulumi.ResourceOptions(
                parent=self, depends_on=[self.cluster_autoscaler_role]
            ),
        )

        self.external_dns_role = self._create_external_dns_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        self.external_dns_sa = self._create_external_dns_service_account(
            name,
            eks.provider,
            self.external_dns_role.arn,
            pulumi.ResourceOptions(
                parent=self, depends_on=[self.gloo_namespace, self.external_dns_role]
            ),
        )

        self.ebs_csi_role = self._create_ebs_csi_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        self.ebs_csi_addon = aws.eks.Addon(
            f"{name}-ebs-csi",
            cluster_name=eks.cluster_name,
            addon_name="aws-ebs-csi-driver",
            service_account_role_arn=self.ebs_csi_role.arn,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self.ebs_csi_role]),
        )

        # Create azrebalance role for suspend-azrebalance cronjob
        self.azrebalance_role = self._create_azrebalance_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        # Create AMP ingest role for Prometheus remote write
        self.amp_ingest_role = self._create_amp_ingest_role(
            name,
            eks.oidc_provider_arn,
            eks.oidc_provider_url,
            child_opts,
        )

        self.register_outputs(
            {
                "gloo_namespace": self.gloo_namespace.metadata.name,
                "alb_controller_role_arn": self.alb_controller_role.arn,
                "cluster_autoscaler_role_arn": self.cluster_autoscaler_role.arn,
                "external_dns_role_arn": self.external_dns_role.arn,
                "ebs_csi_role_arn": self.ebs_csi_role.arn,
                "azrebalance_role_arn": self.azrebalance_role.arn,
                "amp_ingest_role_arn": self.amp_ingest_role.arn,
            }
        )

    def _create_gloo_namespace(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        opts: pulumi.ResourceOptions,
    ) -> k8s.core.v1.Namespace:
        return k8s.core.v1.Namespace(
            f"{name}-gloo-system",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="gloo-system",
                labels={
                    "kubernetes.io/metadata.name": "gloo-system",
                    "name": "gloo-system",
                },
            ),
            opts=pulumi.ResourceOptions(parent=opts.parent, provider=k8s_provider),
        )

    def _create_irsa_trust_policy(
        self,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        namespace: str,
        service_account: str,
    ) -> pulumi.Output[str]:
        return pulumi.Output.all(oidc_arn, oidc_url).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": args[0]},
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringEquals": {
                                    f"{args[1].replace('https://', '')}:sub": f"system:serviceaccount:{namespace}:{service_account}"
                                }
                            },
                        }
                    ],
                }
            )
        )

    def _create_alb_controller_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        role = aws.iam.Role(
            f"{name}-alb-controller-role",
            name=self._resource_suffix.apply(
                lambda s: f"{self.config.resource_prefix}-alb-controller-{s}"
            ),
            assume_role_policy=self._create_irsa_trust_policy(
                oidc_arn, oidc_url, "kube-system", "aws-lb-controller-sa"
            ),
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-alb-controller"),
            opts=opts,
        )

        aws.iam.RolePolicy(
            f"{name}-alb-controller-policy",
            role=role.name,
            policy=json.dumps(AWS_LOAD_BALANCER_POLICY),
            opts=opts,
        )

        return role

    def _create_alb_controller(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        cluster_name: pulumi.Output[str],
        vpc_id: pulumi.Output[str],
        role_arn: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> Release:
        sa = k8s.core.v1.ServiceAccount(
            f"{name}-alb-controller-sa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="aws-lb-controller-sa",
                namespace="kube-system",
                annotations={"eks.amazonaws.com/role-arn": role_arn},
            ),
            opts=pulumi.ResourceOptions(parent=opts.parent, provider=k8s_provider),
        )

        return Release(
            f"{name}-alb-controller",
            ReleaseArgs(
                name="aws-load-balancer-controller",
                chart="aws-load-balancer-controller",
                repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                    repo="https://aws.github.io/eks-charts",
                ),
                namespace="kube-system",
                values={
                    "region": self.config.region,
                    "serviceAccount": {
                        "name": "aws-lb-controller-sa",
                        "create": False,
                    },
                    "vpcId": vpc_id,
                    "clusterName": cluster_name,
                    "podLabels": {"app": "aws-lb-controller"},
                    # use self-signed certificate for webhook (no cert-manager)
                    "enableCertManager": False,
                },
            ),
            opts=pulumi.ResourceOptions(
                parent=opts.parent, provider=k8s_provider, depends_on=[sa]
            ),
        )

    def _create_cluster_autoscaler_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        role = aws.iam.Role(
            f"{name}-cluster-autoscaler-role",
            name=self._resource_suffix.apply(
                lambda s: f"{self.config.resource_prefix}-cluster-autoscaler-{s}"
            ),
            assume_role_policy=self._create_irsa_trust_policy(
                oidc_arn, oidc_url, "kube-system", "cluster-autoscaler-sa"
            ),
            tags=self.config.tags(
                Name=f"{self.config.resource_prefix}-cluster-autoscaler"
            ),
            opts=opts,
        )

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "autoscaling:DescribeAutoScalingGroups",
                        "autoscaling:DescribeAutoScalingInstances",
                        "autoscaling:DescribeLaunchConfigurations",
                        "autoscaling:DescribeScalingActivities",
                        "autoscaling:DescribeTags",
                        "ec2:DescribeInstanceTypes",
                        "ec2:DescribeLaunchTemplateVersions",
                        "ec2:DescribeImages",
                        "ec2:GetInstanceTypesFromInstanceRequirements",
                        "eks:DescribeNodegroup",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "autoscaling:SetDesiredCapacity",
                        "autoscaling:TerminateInstanceInAutoScalingGroup",
                    ],
                    "Resource": "*",
                },
            ],
        }

        aws.iam.RolePolicy(
            f"{name}-cluster-autoscaler-policy",
            role=role.name,
            policy=json.dumps(policy),
            opts=opts,
        )

        return role

    def _create_cluster_autoscaler(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        cluster_name: pulumi.Output[str],
        role_arn: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> Release:
        return Release(
            f"{name}-cas",
            ReleaseArgs(
                name="cluster-autoscaler",
                chart="cluster-autoscaler",
                repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                    repo="https://kubernetes.github.io/autoscaler",
                ),
                version="9.29.3",
                namespace="kube-system",
                values={
                    "awsRegion": self.config.region,
                    "autoDiscovery": {"clusterName": cluster_name},
                    "replicaCount": 2,
                    "rbac": {
                        "serviceAccount": {
                            "create": True,
                            "name": "cluster-autoscaler-sa",
                            "annotations": {"eks.amazonaws.com/role-arn": role_arn},
                        }
                    },
                    "extraArgs": {
                        "balance-similar-node-groups": "false",
                        "skip-nodes-with-local-storage": "false",
                        "scale-down-delay-after-add": "30s",
                        "scale-down-delay-after-delete": "0s",
                        "scale-down-unneeded-time": "30s",
                        "max-node-provision-time": "10m",
                        "expander": "priority",
                    },
                    "expanderPriorities": {
                        10: [".*default.*"],
                        1: [".*"],
                    },
                },
            ),
            opts=pulumi.ResourceOptions(parent=opts.parent, provider=k8s_provider),
        )

    def _create_external_dns_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        trust_policy = pulumi.Output.all(oidc_arn, oidc_url).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": args[0]},
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringEquals": {
                                    f"{args[1].replace('https://', '')}:sub": [
                                        "system:serviceaccount:gloo-system:external-dns",
                                        "system:serviceaccount:gloo-system:certmanager-certgen",
                                    ]
                                }
                            },
                        }
                    ],
                }
            )
        )

        role = aws.iam.Role(
            f"{name}-external-dns-role",
            name=self._resource_suffix.apply(
                lambda s: f"{self.config.resource_prefix}-external-dns-{s}"
            ),
            assume_role_policy=trust_policy,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-external-dns"),
            opts=opts,
        )

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "route53:ChangeResourceRecordSets",
                    ],
                    "Resource": "arn:aws:route53:::hostedzone/*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "route53:ListHostedZones",
                        "route53:ListHostedZonesByName",
                        "route53:ListResourceRecordSets",
                        "route53:GetChange",
                    ],
                    "Resource": "*",
                },
            ],
        }

        aws.iam.RolePolicy(
            f"{name}-external-dns-policy",
            role=role.name,
            policy=json.dumps(policy),
            opts=opts,
        )

        return role

    def _create_external_dns_service_account(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        role_arn: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> k8s.core.v1.ServiceAccount:
        return k8s.core.v1.ServiceAccount(
            f"{name}-external-dns-sa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="external-dns",
                namespace="gloo-system",
                annotations={"eks.amazonaws.com/role-arn": role_arn},
            ),
            opts=pulumi.ResourceOptions(parent=opts.parent, provider=k8s_provider),
        )

    def _create_ebs_csi_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        trust_policy = pulumi.Output.all(oidc_arn, oidc_url).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": args[0]},
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringEquals": {
                                    f"{args[1].replace('https://', '')}:aud": "sts.amazonaws.com",
                                    f"{args[1].replace('https://', '')}:sub": "system:serviceaccount:kube-system:ebs-csi-controller-sa",
                                }
                            },
                        }
                    ],
                }
            )
        )

        role = aws.iam.Role(
            f"{name}-ebs-csi-role",
            name=self._resource_suffix.apply(
                lambda s: f"{self.config.resource_prefix}-ebs-csi-{s}"
            ),
            assume_role_policy=trust_policy,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-ebs-csi"),
            opts=opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-ebs-csi-policy",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy",
            opts=opts,
        )

        return role

    def _create_azrebalance_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        """Create IAM role for suspend-azrebalance cronjob to manage ASG processes."""
        role_name = self._cell_name.apply(
            lambda cn: f"control-plane-azrebalance-role-{cn}"
        )
        role = aws.iam.Role(
            f"{name}-azrebalance-role",
            name=role_name,
            assume_role_policy=self._create_irsa_trust_policy(
                oidc_arn, oidc_url, "pc-control-plane", "suspend-azrebalance-sa"
            ),
            tags=role_name.apply(lambda rn: self.config.tags(Name=rn)),
            opts=opts,
        )

        aws.iam.RolePolicy(
            f"{name}-azrebalance-policy",
            role=role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "autoscaling:DescribeAutoScalingGroups",
                                "autoscaling:SuspendProcesses",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=opts,
        )

        return role

    def _create_amp_ingest_role(
        self,
        name: str,
        oidc_arn: pulumi.Output[str],
        oidc_url: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        """Create IAM role for Prometheus to assume for AMP remote write."""
        return aws.iam.Role(
            f"{name}-amp-ingest-role",
            name=self._resource_suffix.apply(
                lambda s: f"{self.config.resource_prefix}-amp-ingest-{s}"
            ),
            assume_role_policy=self._create_irsa_trust_policy(
                oidc_arn, oidc_url, "prometheus", "amp-iamproxy-ingest-service-account"
            ),
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-amp-ingest"),
            opts=opts,
        )
