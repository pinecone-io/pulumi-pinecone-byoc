"""Azure-specific configuration for BYOC infrastructure."""

from pydantic import BaseModel, Field

from .base import BaseConfig


class FlexibleServerInstanceConfig(BaseModel):
    name: str
    db_name: str
    username: str
    sku_name: str = "Standard_D2s_v3"


class FlexibleServerConfig(BaseModel):
    deletion_protection: bool = False

    control_db: FlexibleServerInstanceConfig = FlexibleServerInstanceConfig(
        name="control-db",
        db_name="controller",
        username="controller",
    )

    system_db: FlexibleServerInstanceConfig = FlexibleServerInstanceConfig(
        name="system-db",
        db_name="systemdb",
        username="systemuser",
    )


class AzureConfig(BaseConfig):
    cloud: str = "azure"
    subscription_id: str = ""

    database: FlexibleServerConfig = Field(default_factory=FlexibleServerConfig)
    custom_tags: dict[str, str] = Field(default_factory=dict)

    def tags(self, **extra: str) -> dict[str, str]:
        base_tags = {
            "pinecone-managed-by": "pulumi",
        }
        return {**base_tags, **self.custom_tags, **extra}
