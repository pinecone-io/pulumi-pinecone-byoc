"""
Clean configuration for Pinecone BYOC AWS infrastructure.
"""

from typing import Optional
from pydantic import BaseModel, Field
import pulumi


class NodePoolTaint(BaseModel):
    key: str
    value: str
    effect: str = "NoSchedule"


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
    deletion_protection: bool = True
    backup_retention_days: int = 7


class DatabaseConfig(BaseModel):
    """RDS database configuration with control-db and system-db instances."""

    engine_version: str = "15.15"
    deletion_protection: bool = True
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

    cell_name: str
    region: str
    environment: str = "dev"
    subdomain: str

    # Pinecone organization
    organization_id: str

    # Pinecone API settings
    api_url: str = "https://api.pinecone.io"

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
    parent_zone_id: Optional[str] = None
    parent_zone_name: str = "pinecone.io"

    @classmethod
    def from_pulumi(cls) -> "Config":
        """Load configuration from Pulumi config."""
        config = pulumi.Config()

        # Required values
        cell_name = config.require("cell_name")
        region = config.require("region")
        subdomain = config.require("subdomain")
        availability_zones = config.require_object("availability_zones")
        organization_id = config.require("organization_id")

        # Optional values with defaults
        environment = config.get("environment") or "dev"
        kubernetes_version = config.get("kubernetes_version") or "1.30"
        vpc_cidr = config.get("vpc_cidr") or "10.0.0.0/16"
        parent_zone_id = config.get("parent_zone_id")
        parent_zone_name = config.get("parent_zone_name") or "pinecone.io"
        api_url = config.get("api_url") or "https://api.pinecone.io"

        # Parse node pools
        node_pools_raw = config.get_object("node_pools") or []
        node_pools = [NodePoolConfig(**np) for np in node_pools_raw]

        # Parse database config
        db_raw = config.get_object("database") or {}
        database = DatabaseConfig(**db_raw)

        return cls(
            cell_name=cell_name,
            region=region,
            environment=environment,
            subdomain=subdomain,
            organization_id=organization_id,
            api_url=api_url,
            availability_zones=availability_zones,
            kubernetes_version=kubernetes_version,
            vpc_cidr=vpc_cidr,
            parent_zone_id=parent_zone_id,
            parent_zone_name=parent_zone_name,
            node_pools=node_pools,
            database=database,
        )

    @property
    def is_production(self) -> bool:
        return self.environment in ("prod", "production")

    @property
    def resource_prefix(self) -> str:
        return f"pc-{self.cell_name}"

    @property
    def fqdn(self) -> str:
        return f"{self.subdomain}.{self.parent_zone_name}"

    def tags(self, **extra: str) -> dict[str, str]:
        """Generate consistent resource tags."""
        base_tags = {
            "pinecone:cell": self.cell_name,
            "pinecone:environment": self.environment,
            "pinecone:managed-by": "pulumi",
        }
        return {**base_tags, **extra}
