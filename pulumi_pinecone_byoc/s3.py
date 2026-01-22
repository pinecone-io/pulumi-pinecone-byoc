"""
S3 component for Pinecone BYOC infrastructure.

Creates S3 buckets for vector data storage, WAL, and operational data.
"""

from typing import Optional

import pulumi
import pulumi_aws as aws

from config import Config


class S3Buckets(pulumi.ComponentResource):
    """
    Creates S3 buckets with:
    - Data bucket for vector storage
    - WAL bucket for write-ahead logs
    - Internal bucket for operational data
    - Server-side encryption (AES256)
    - Lifecycle rules for multipart upload cleanup
    - Optional versioning for production
    """

    def __init__(
        self,
        name: str,
        config: Config,
        cell_name: pulumi.Input[str],
        kms_key_arn: Optional[pulumi.Output[str]] = None,
        force_destroy: bool = False,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:S3Buckets", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        self._force_destroy = force_destroy
        child_opts = pulumi.ResourceOptions(parent=self)

        # bucket naming follows reference pattern: pc-{type}-{cell_name}
        # Data bucket - main vector storage
        self.data_bucket = self._create_bucket(
            name=f"{name}-data",
            bucket_name=self._cell_name.apply(lambda cn: f"pc-data-{cn}"),
            enable_versioning=True,
            kms_key_arn=kms_key_arn,
            opts=child_opts,
        )

        # Index backups bucket
        self.index_backups_bucket = self._create_bucket(
            name=f"{name}-index-backups",
            bucket_name=self._cell_name.apply(lambda cn: f"pc-index-backups-{cn}"),
            enable_versioning=False,
            kms_key_arn=kms_key_arn,
            opts=child_opts,
        )

        # WAL bucket - write-ahead logs
        self.wal_bucket = self._create_bucket(
            name=f"{name}-wal",
            bucket_name=self._cell_name.apply(lambda cn: f"pc-wal-{cn}"),
            enable_versioning=False,
            kms_key_arn=kms_key_arn,
            opts=child_opts,
        )

        # Janitor bucket - cleanup operations
        self.janitor_bucket = self._create_bucket(
            name=f"{name}-janitor",
            bucket_name=self._cell_name.apply(lambda cn: f"pc-janitor-{cn}"),
            enable_versioning=False,
            kms_key_arn=kms_key_arn,
            opts=child_opts,
        )

        # Internal bucket - operational data
        self.internal_bucket = self._create_bucket(
            name=f"{name}-internal",
            bucket_name=self._cell_name.apply(lambda cn: f"pc-internal-{cn}"),
            enable_versioning=False,
            kms_key_arn=kms_key_arn,
            opts=child_opts,
        )

        self.register_outputs(
            {
                "data_bucket_name": self.data_bucket.id,
                "data_bucket_arn": self.data_bucket.arn,
                "index_backups_bucket_name": self.index_backups_bucket.id,
                "wal_bucket_name": self.wal_bucket.id,
                "janitor_bucket_name": self.janitor_bucket.id,
                "internal_bucket_name": self.internal_bucket.id,
            }
        )

    def _create_bucket(
        self,
        name: str,
        bucket_name: pulumi.Input[str],
        enable_versioning: bool,
        kms_key_arn: Optional[pulumi.Output[str]] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> aws.s3.Bucket:
        """Create an S3 bucket with standard configuration."""
        bucket = aws.s3.Bucket(
            name,
            bucket=bucket_name,
            force_destroy=self._force_destroy,
            tags=pulumi.Output.from_input(bucket_name).apply(
                lambda bn: self.config.tags(Name=bn)
            ),
            opts=opts,
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-public-access-block",
            bucket=bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=opts,
        )

        if kms_key_arn:
            aws.s3.BucketServerSideEncryptionConfiguration(
                f"{name}-encryption",
                bucket=bucket.id,
                rules=[
                    aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                        apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                            sse_algorithm="aws:kms",
                            kms_master_key_id=kms_key_arn,
                        ),
                        bucket_key_enabled=True,
                    ),
                ],
                opts=opts,
            )
        else:
            aws.s3.BucketServerSideEncryptionConfiguration(
                f"{name}-encryption",
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

        if enable_versioning:
            aws.s3.BucketVersioning(
                f"{name}-versioning",
                bucket=bucket.id,
                versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                    status="Enabled",
                ),
                opts=opts,
            )

        aws.s3.BucketLifecycleConfiguration(
            f"{name}-lifecycle",
            bucket=bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationRuleArgs(
                    id="abort-incomplete-multipart",
                    status="Enabled",
                    abort_incomplete_multipart_upload=aws.s3.BucketLifecycleConfigurationRuleAbortIncompleteMultipartUploadArgs(
                        days_after_initiation=2,
                    ),
                ),
            ],
            opts=opts,
        )

        return bucket

    @property
    def data_bucket_name(self) -> pulumi.Output[str]:
        return self.data_bucket.id

    @property
    def data_bucket_arn(self) -> pulumi.Output[str]:
        return self.data_bucket.arn

    @property
    def index_backups_bucket_name(self) -> pulumi.Output[str]:
        return self.index_backups_bucket.id

    @property
    def wal_bucket_name(self) -> pulumi.Output[str]:
        return self.wal_bucket.id

    @property
    def janitor_bucket_name(self) -> pulumi.Output[str]:
        return self.janitor_bucket.id

    @property
    def internal_bucket_name(self) -> pulumi.Output[str]:
        return self.internal_bucket.id
