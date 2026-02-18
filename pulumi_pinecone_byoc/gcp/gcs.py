"""GCS buckets for GCP infrastructure."""

import pulumi
import pulumi_gcp as gcp

from config.gcp import GCPConfig


class GCSBuckets(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: GCPConfig,
        cell_name: pulumi.Input[str],
        force_destroy: bool = False,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:GCSBuckets", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        self._force_destroy = force_destroy
        child_opts = pulumi.ResourceOptions(parent=self)

        self.data_bucket = self._create_bucket(
            name=f"{name}-data",
            bucket_type="data",
            opts=child_opts,
        )

        self.index_backups_bucket = self._create_bucket(
            name=f"{name}-index-backups",
            bucket_type="index-backups",
            opts=child_opts,
        )

        self.wal_bucket = self._create_bucket(
            name=f"{name}-wal",
            bucket_type="wal",
            opts=child_opts,
        )

        self.janitor_bucket = self._create_bucket(
            name=f"{name}-janitor",
            bucket_type="janitor",
            opts=child_opts,
        )

        self.internal_bucket = self._create_bucket(
            name=f"{name}-internal",
            bucket_type="internal",
            opts=child_opts,
        )

        self.register_outputs(
            {
                "data_bucket_name": self.data_bucket.name,
                "index_backups_bucket_name": self.index_backups_bucket.name,
                "wal_bucket_name": self.wal_bucket.name,
                "janitor_bucket_name": self.janitor_bucket.name,
                "internal_bucket_name": self.internal_bucket.name,
            }
        )

    def _create_bucket(
        self,
        name: str,
        bucket_type: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> gcp.storage.Bucket:
        full_bucket_name = self._cell_name.apply(lambda cn: f"pc-{bucket_type}-{cn}")

        bucket = gcp.storage.Bucket(
            name,
            name=full_bucket_name,
            project=self.config.project,
            location=self.config.region,
            force_destroy=self._force_destroy,
            uniform_bucket_level_access=True,
            versioning=gcp.storage.BucketVersioningArgs(enabled=True),
            lifecycle_rules=[
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(
                        type="AbortIncompleteMultipartUpload",
                    ),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(age=1),
                ),
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        days_since_noncurrent_time=3,
                    ),
                ),
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        age=30,
                        matches_prefixes=["activity-scrapes/"],
                    ),
                ),
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        age=7,
                        matches_prefixes=["janitor/"],
                    ),
                ),
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        age=14,
                        matches_prefixes=["lag-reporter/"],
                    ),
                ),
            ],
            labels=self.config.labels(),
            opts=opts,
        )

        return bucket

    @property
    def data_bucket_name(self) -> pulumi.Output[str]:
        return self.data_bucket.name

    @property
    def index_backups_bucket_name(self) -> pulumi.Output[str]:
        return self.index_backups_bucket.name

    @property
    def wal_bucket_name(self) -> pulumi.Output[str]:
        return self.wal_bucket.name

    @property
    def janitor_bucket_name(self) -> pulumi.Output[str]:
        return self.janitor_bucket.name

    @property
    def internal_bucket_name(self) -> pulumi.Output[str]:
        return self.internal_bucket.name
