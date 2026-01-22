"""
Pulumi Kubernetes Operator setup for self-managed BYOC.

Creates S3 backend, KMS key for secrets, and IRSA for the operator.
"""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws

from config import Config


class PulumiOperator(pulumi.ComponentResource):
    """
    Sets up pulumi-k8s-operator to use S3 backend instead of Pulumi Cloud.

    Creates:
    - S3 bucket for Pulumi state storage
    - KMS key for encrypting Pulumi secrets
    - IRSA role with S3/KMS permissions

    Note: The ServiceAccount is created by the Helm chart via helmfile config
    which passes pulumi_operator_role_arn for the IRSA annotation.
    """

    def __init__(
        self,
        name: str,
        config: Config,
        oidc_provider_arn: pulumi.Input[str],
        oidc_provider_url: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        operator_namespace: str = "pulumi-kubernetes-operator",
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:PulumiOperator", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        # state bucket for pulumi backend
        self._state_bucket = self._create_state_bucket(name, child_opts)

        # kms key for secrets encryption
        self._kms_key = self._create_kms_key(name, child_opts)

        # irsa role for the operator (helm chart creates SA with this role via helmfile)
        self._operator_role = self._create_operator_role(
            name,
            oidc_provider_arn,
            oidc_provider_url,
            operator_namespace,
            child_opts,
        )

        # build backend url
        self._backend_url = pulumi.Output.all(
            self._state_bucket.bucket,
            config.region,
        ).apply(lambda args: f"s3://{args[0]}?region={args[1]}&awssdk=v2")

        # build secrets provider url
        self._secrets_provider = pulumi.Output.all(
            self._kms_key.arn,
            config.region,
        ).apply(lambda args: f"awskms:///{args[0]}?region={args[1]}")

        self.register_outputs(
            {
                "state_bucket_name": self._state_bucket.id,
                "kms_key_arn": self._kms_key.arn,
                "operator_role_arn": self._operator_role.arn,
                "backend_url": self._backend_url,
                "secrets_provider": self._secrets_provider,
                "service_account_name": "pulumi",
                "namespace": operator_namespace,
            }
        )

    def _create_state_bucket(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> aws.s3.Bucket:
        """Create S3 bucket for Pulumi state storage."""
        bucket_name = self._cell_name.apply(lambda cn: f"pc-pulumi-state-{cn}")

        bucket = aws.s3.Bucket(
            f"{name}-state-bucket",
            bucket=bucket_name,
            force_destroy=True,
            tags=bucket_name.apply(lambda bn: self.config.tags(Name=bn)),
            opts=opts,
        )

        # block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-state-bucket-public-access-block",
            bucket=bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=opts,
        )

        # enable versioning for state files
        aws.s3.BucketVersioning(
            f"{name}-state-bucket-versioning",
            bucket=bucket.id,
            versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                status="Enabled",
            ),
            opts=opts,
        )

        # server-side encryption with AES256 (state itself isn't sensitive)
        aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-state-bucket-encryption",
            bucket=bucket.id,
            rules=[
                aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                    apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                        sse_algorithm="AES256",
                    ),
                ),
            ],
            opts=opts,
        )

        # lifecycle rules
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-state-bucket-lifecycle",
            bucket=bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="abort-incomplete-multipart",
                    status="Enabled",
                    abort_incomplete_multipart_upload=aws.s3.BucketLifecycleConfigurationRuleAbortIncompleteMultipartUploadArgs(
                        days_after_initiation=2,
                    ),
                ),
                # keep old state versions for 30 days
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="expire-old-versions",
                    status="Enabled",
                    noncurrent_version_expiration=aws.s3.BucketLifecycleConfigurationRuleNoncurrentVersionExpirationArgs(
                        noncurrent_days=30,
                    ),
                ),
            ],
            opts=opts,
        )

        return bucket

    def _create_kms_key(self, name: str, opts: pulumi.ResourceOptions) -> aws.kms.Key:
        """Create KMS key for encrypting Pulumi secrets."""
        key = aws.kms.Key(
            f"{name}-pulumi-secrets-key",
            description=self._cell_name.apply(
                lambda cn: f"KMS key for Pulumi secrets encryption - {cn}"
            ),
            enable_key_rotation=True,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-pulumi-secrets"),
            opts=opts,
        )

        aws.kms.Alias(
            f"{name}-pulumi-secrets-key-alias",
            name=f"alias/{self.config.resource_prefix}-pulumi-secrets",
            target_key_id=key.id,
            opts=opts,
        )

        return key

    def _create_operator_role(
        self,
        name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_provider_url: pulumi.Input[str],
        namespace: str,
        opts: pulumi.ResourceOptions,
    ) -> aws.iam.Role:
        """Create IAM role for pulumi-k8s-operator with IRSA."""
        # build trust policy for irsa - allow all service accounts in the namespace
        # (both 'pulumi' for Stack workloads and 'pulumi-k8s-operator' for the operator itself)
        assume_role_policy = pulumi.Output.all(
            oidc_provider_arn,
            oidc_provider_url,
        ).apply(
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
                                },
                                "StringLike": {
                                    f"{args[1].replace('https://', '')}:sub": f"system:serviceaccount:{namespace}:*",
                                },
                            },
                        }
                    ],
                }
            )
        )

        role = aws.iam.Role(
            f"{name}-operator-role",
            assume_role_policy=assume_role_policy,
            tags=self.config.tags(
                Name=f"{self.config.resource_prefix}-pulumi-operator"
            ),
            opts=opts,
        )

        # s3 permissions for state bucket
        s3_policy = aws.iam.Policy(
            f"{name}-operator-s3-policy",
            description="Pulumi operator S3 state bucket access",
            policy=self._state_bucket.arn.apply(
                lambda bucket_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:DeleteObject",
                                    "s3:ListBucket",
                                    "s3:GetBucketLocation",
                                ],
                                "Resource": [
                                    bucket_arn,
                                    f"{bucket_arn}/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-operator-s3-attach",
            role=role.name,
            policy_arn=s3_policy.arn,
            opts=opts,
        )

        # kms permissions for secrets encryption
        kms_policy = aws.iam.Policy(
            f"{name}-operator-kms-policy",
            description="Pulumi operator KMS secrets encryption",
            policy=self._kms_key.arn.apply(
                lambda key_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "kms:Encrypt",
                                    "kms:Decrypt",
                                    "kms:GenerateDataKey",
                                    "kms:DescribeKey",
                                ],
                                "Resource": key_arn,
                            }
                        ],
                    }
                )
            ),
            opts=opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-operator-kms-attach",
            role=role.name,
            policy_arn=kms_policy.arn,
            opts=opts,
        )

        # eks/ec2 permissions for managing nodepools
        # matches pinecone-platform/pulumi/satellites/pinecone/k8s/aws_k8s.py
        eks_policy = aws.iam.Policy(
            f"{name}-operator-eks-policy",
            description="Pulumi operator EKS nodepool management",
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                # eks node group management
                                "eks:Describe*",
                                "eks:CreateNodegroup",
                                "eks:DeleteNodegroup",
                                "eks:ListNodegroups",
                                "eks:UpdateNodegroupConfig",
                                "eks:UpdateNodegroupVersion",
                                "eks:TagResource",
                                "eks:UntagResource",
                                # launch template and instance management
                                "ec2:Describe*",
                                "ec2:CreateLaunchTemplate",
                                "ec2:CreateLaunchTemplateVersion",
                                "ec2:ModifyLaunchTemplate",
                                "ec2:DeleteLaunchTemplate",
                                "ec2:RunInstances",
                                # autoscaling tags
                                "autoscaling:CreateOrUpdateTags",
                                "autoscaling:DescribeTags",
                                "autoscaling:DeleteTags",
                                # pass role for node groups
                                "iam:PassRole",
                                "iam:GetRole",
                                "iam:ListAttachedRolePolicies",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=opts,
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-operator-eks-attach",
            role=role.name,
            policy_arn=eks_policy.arn,
            opts=opts,
        )

        return role

    @property
    def kms_key_arn(self) -> pulumi.Output[str]:
        return self._kms_key.arn

    @property
    def backend_url(self) -> pulumi.Output[str]:
        """S3 backend URL for Stack CRD spec.backend field."""
        return self._backend_url

    @property
    def secrets_provider(self) -> pulumi.Output[str]:
        """KMS secrets provider URL for Stack CRD spec.secretsProvider field."""
        return self._secrets_provider

    @property
    def operator_role_arn(self) -> pulumi.Output[str]:
        """IAM role ARN for pulumi-k8s-operator service account IRSA."""
        return self._operator_role.arn

    @property
    def namespace(self) -> str:
        return "pulumi-kubernetes-operator"
