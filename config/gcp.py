"""GCP-specific configuration for BYOC infrastructure."""

from pydantic import BaseModel, Field

from .base import BaseConfig


class AlloyDBInstanceConfig(BaseModel):
    name: str
    db_name: str
    username: str
    cpu_count: int = 2


class AlloyDBConfig(BaseModel):
    deletion_protection: bool = False

    control_db: AlloyDBInstanceConfig = AlloyDBInstanceConfig(
        name="control-db",
        db_name="controller",
        username="controller",
    )

    system_db: AlloyDBInstanceConfig = AlloyDBInstanceConfig(
        name="system-db",
        db_name="systemdb",
        username="systemuser",
    )


class GCPConfig(BaseConfig):
    cloud: str = "gcp"
    project: str = ""

    writer_k8s_service_accounts: list[str] = Field(
        default_factory=lambda: [
            "pc-admin/admin-sa",
            "pc-admission-control/admission-control-sa",
            "pc-backup-worker/backup-worker-sa",
            "pc-control-plane/control-planes-sa",
            "pc-data-importer/data-importer-pitboss-sa",
            "pc-data-importer/data-importer-sa",
            "pc-docs-api/docs-api-sa",
            "pc-heartbeat/heartbeat-sa",
            "pc-index-builder-slab/index-builders-sa",
            "pc-index-builder-slab/index-builders-slab-sa",
            "pc-janitor/janitor-sa",
            "pc-query-executors-slab/query-executors-slab-prov-sa",
            "pc-query-executors-slab/query-executors-slab-sa",
            "pc-query-executors-slab/query-executors-slab-shared-sa",
            "pc-query-routers/query-routers-sa",
            "pc-request-log-writers/request-log-writers-sa",
            "pc-shard-manager/shard-manager-sa",
            "prometheus/metrics-proxy-sa",
            "tooling/tooling-sa",
        ]
    )
    reader_k8s_service_accounts: list[str] = Field(
        default_factory=lambda: [
            "gloo-system/netstack-sa",
        ]
    )

    database: AlloyDBConfig = Field(default_factory=AlloyDBConfig)
    custom_tags: dict[str, str] = Field(default_factory=dict)

    def labels(self, **extra: str) -> dict[str, str]:
        base_labels = {
            "pinecone-managed-by": "pulumi",
        }
        return {**base_labels, **self.custom_tags, **extra}

