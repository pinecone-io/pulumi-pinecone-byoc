"""
Configuration for Pinecone BYOC AWS infrastructure.
"""

from pydantic import Field

from .base import BaseConfig

APN_TAG_PROD_BYOC = ("aws-apn-id", "pc:5eldspisdx06ewzohqetnxufm")
APN_TAG_NONPROD_BYOC = ("aws-test-apn-id", "pc:00000000000000000000-byoc")


class AWSConfig(BaseConfig):
    """
    AWS-specific configuration for BYOC infrastructure.

    Extends BaseConfig with AWS-specific settings.
    """

    cloud: str = "aws"

    # Networking

    existing_vpc_id: str | None = None

    public_access: bool = True

    existing_route_table_ids: dict[str, str] | None = None

    public_subnet_ids: list[str] | None = None
    private_subnet_ids: list[str] | None = None

    # Custom AMI
    custom_ami_id: str | None = None

    # KMS key ARN for encrypting S3
    kms_key_arn: str | None = None

    # Custom tags from user
    custom_tags: dict[str, str] = Field(default_factory=dict)

    def tags(self, **extra: str) -> dict[str, str]:
        """Generate consistent resource tags, including user-provided custom tags."""
        apn_key, apn_value = (
            APN_TAG_PROD_BYOC if self.global_env == "prod" else APN_TAG_NONPROD_BYOC
        )
        base_tags = {
            "pinecone:managed-by": "pulumi",
            apn_key: apn_value,
        }
        return {**base_tags, **self.custom_tags, **extra}
