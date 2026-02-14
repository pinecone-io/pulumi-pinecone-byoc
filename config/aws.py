"""
Configuration for Pinecone BYOC AWS infrastructure.
"""

from pydantic import BaseModel, Field

from .base import BaseConfig


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


class AWSConfig(BaseConfig):
    """
    AWS-specific configuration for BYOC infrastructure.

    Extends BaseConfig with AWS-specific settings.
    """

    cloud: str = "aws"

    # Networking
    public_subnet_mask: int = 20
    private_subnet_mask: int = 18

    # Database
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # Custom AMI
    custom_ami_id: str | None = None

    # Custom tags from user
    custom_tags: dict[str, str] = Field(default_factory=dict)

    def tags(self, **extra: str) -> dict[str, str]:
        """Generate consistent resource tags, including user-provided custom tags."""
        base_tags = {
            "pinecone:managed-by": "pulumi",
        }
        return {**base_tags, **self.custom_tags, **extra}
