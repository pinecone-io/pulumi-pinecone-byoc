"""
Configuration for Pinecone BYOC AWS infrastructure.
"""

from pydantic import BaseModel, Field


class NodePoolTaint(BaseModel):
    key: str
    value: str
    effect: str = (
        "NO_SCHEDULE"  # AWS format: NO_SCHEDULE, PREFER_NO_SCHEDULE, NO_EXECUTE
    )


class NodePoolConfig(BaseModel):
    name: str
    instance_type: str = "r6in.large"
    min_size: int = 1
    max_size: int = 10
    desired_size: int = 3
    disk_size_gb: int = 100
    disk_type: str = "gp3"
    labels: dict[str, str] = Field(default_factory=dict)
    taints: list[NodePoolTaint] = Field(default_factory=list)


class DatabaseInstanceConfig(BaseModel):
    """Configuration for a single RDS instance."""

    name: str
    db_name: str
    username: str
    instance_class: str = "db.r8g.large"
    engine_version: str = "15.15"
    deletion_protection: bool = False
    backup_retention_days: int = 7


class DatabaseConfig(BaseModel):
    """RDS database configuration with control-db and system-db instances."""

    engine_version: str = "15.15"
    deletion_protection: bool = False
    backup_retention_days: int = 7

    # Control database (1 shard)
    control_db: DatabaseInstanceConfig = DatabaseInstanceConfig(
        name="control-db",
        db_name="controller",
        username="controller",
        instance_class="db.r8g.large",
    )

    # System database
    system_db: DatabaseInstanceConfig = DatabaseInstanceConfig(
        name="system-db",
        db_name="systemdb",
        username="systemuser",
        instance_class="db.r8g.large",
    )


class Config(BaseModel):
    """
    Main configuration for BYOC infrastructure.

    All settings are loaded from Pulumi config with sensible defaults.
    """

    region: str
    environment: str = "dev"
    global_env: str = "prod"
    cloud: str = "aws"

    # Networking
    availability_zones: list[str]
    vpc_cidr: str = "10.0.0.0/16"
    public_subnet_mask: int = 20
    private_subnet_mask: int = 18

    # Kubernetes
    kubernetes_version: str = "1.33"
    node_pools: list[NodePoolConfig] = Field(default_factory=list)

    # Database
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # DNS
    parent_zone_name: str = "pinecone.io"

    @property
    def resource_prefix(self) -> str:
        return "pc"

    def tags(self, **extra: str) -> dict[str, str]:
        """Generate consistent resource tags."""
        base_tags = {
            "pinecone:managed-by": "pulumi",
        }
        return {**base_tags, **extra}
