"""Azure Blob Storage for Azure infrastructure."""

import pulumi
import pulumi_azure_native as azure_native

from config.azure import AzureConfig

# azure storage account names: 3-24 chars, lowercase alphanumeric only
STORAGE_ACCOUNT_NAME_LIMIT = 24

CONTAINER_TYPES = ["data", "wal", "index-backups", "janitor", "internal"]


class BlobStorage(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        cell_name: pulumi.Input[str],
        resource_group_name: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:BlobStorage", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        self._resource_group_name = pulumi.Output.from_input(resource_group_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        # storage account name must be lowercase alphanumeric, 3-24 chars
        account_name = self._cell_name.apply(
            lambda cn: f"pc{cn.replace('-', '')}"[:STORAGE_ACCOUNT_NAME_LIMIT]
        )

        self.storage_account = azure_native.storage.StorageAccount(
            f"{name}-account",
            account_name=account_name,
            resource_group_name=resource_group_name,
            access_tier=azure_native.storage.AccessTier.HOT,
            allow_blob_public_access=False,
            allow_shared_key_access=True,
            sku=azure_native.storage.SkuArgs(
                name="Standard_LRS",
            ),
            kind="StorageV2",
            tags=self.config.tags(),
            opts=child_opts,
        )

        self.access_key = pulumi.Output.all(
            self.storage_account.name, self._resource_group_name
        ).apply(
            lambda args: (
                azure_native.storage.list_storage_account_keys(
                    account_name=args[0],
                    resource_group_name=args[1],
                )
                .keys[0]
                .value
            )
        )

        azure_native.storage.BlobServiceProperties(
            f"{name}-blob-service",
            blob_services_name="default",
            account_name=self.storage_account.name,
            resource_group_name=resource_group_name,
            is_versioning_enabled=True,
            delete_retention_policy=azure_native.storage.DeleteRetentionPolicyArgs(
                enabled=True,
                days=3,
            ),
            opts=child_opts,
        )

        # blob containers: pc-{type}-{cell_name}
        self.containers: dict[str, azure_native.storage.BlobContainer] = {}
        for container_type in CONTAINER_TYPES:
            container = azure_native.storage.BlobContainer(
                f"{name}-{container_type}",
                account_name=self.storage_account.name,
                container_name=self._cell_name.apply(lambda cn, ct=container_type: f"pc-{ct}-{cn}"),
                resource_group_name=resource_group_name,
                opts=child_opts,
            )
            self.containers[container_type] = container

        # azure lifecycle prefix_match requires {container-name}/{blob-prefix} format
        data_container_name = self.containers["data"].name
        janitor_container_name = self.containers["janitor"].name
        internal_container_name = self.containers["internal"].name

        azure_native.storage.ManagementPolicy(
            f"{name}-lifecycle",
            account_name=self.storage_account.name,
            resource_group_name=resource_group_name,
            management_policy_name="default",
            policy=azure_native.storage.ManagementPolicySchemaArgs(
                rules=[
                    azure_native.storage.ManagementPolicyRuleArgs(
                        enabled=True,
                        name="delete-old-versions",
                        type="Lifecycle",
                        definition=azure_native.storage.ManagementPolicyDefinitionArgs(
                            actions=azure_native.storage.ManagementPolicyActionArgs(
                                version=azure_native.storage.ManagementPolicyVersionArgs(
                                    delete=azure_native.storage.DateAfterCreationArgs(
                                        days_after_creation_greater_than=3,
                                    ),
                                ),
                            ),
                            filters=azure_native.storage.ManagementPolicyFilterArgs(
                                blob_types=["blockBlob", "appendBlob"],
                            ),
                        ),
                    ),
                    azure_native.storage.ManagementPolicyRuleArgs(
                        enabled=True,
                        name="delete-activity-scrapes",
                        type="Lifecycle",
                        definition=azure_native.storage.ManagementPolicyDefinitionArgs(
                            actions=azure_native.storage.ManagementPolicyActionArgs(
                                base_blob=azure_native.storage.ManagementPolicyBaseBlobArgs(
                                    delete=azure_native.storage.DateAfterModificationArgs(
                                        days_after_creation_greater_than=30,
                                    ),
                                ),
                            ),
                            filters=azure_native.storage.ManagementPolicyFilterArgs(
                                blob_types=["blockBlob", "appendBlob"],
                                prefix_match=[
                                    data_container_name.apply(lambda cn: f"{cn}/activity-scrapes/")
                                ],
                            ),
                        ),
                    ),
                    azure_native.storage.ManagementPolicyRuleArgs(
                        enabled=True,
                        name="delete-janitor",
                        type="Lifecycle",
                        definition=azure_native.storage.ManagementPolicyDefinitionArgs(
                            actions=azure_native.storage.ManagementPolicyActionArgs(
                                base_blob=azure_native.storage.ManagementPolicyBaseBlobArgs(
                                    delete=azure_native.storage.DateAfterModificationArgs(
                                        days_after_creation_greater_than=7,
                                    ),
                                ),
                            ),
                            filters=azure_native.storage.ManagementPolicyFilterArgs(
                                blob_types=["blockBlob", "appendBlob"],
                                prefix_match=[janitor_container_name.apply(lambda cn: f"{cn}/")],
                            ),
                        ),
                    ),
                    azure_native.storage.ManagementPolicyRuleArgs(
                        enabled=True,
                        name="delete-lag-reporter",
                        type="Lifecycle",
                        definition=azure_native.storage.ManagementPolicyDefinitionArgs(
                            actions=azure_native.storage.ManagementPolicyActionArgs(
                                base_blob=azure_native.storage.ManagementPolicyBaseBlobArgs(
                                    delete=azure_native.storage.DateAfterModificationArgs(
                                        days_after_creation_greater_than=14,
                                    ),
                                ),
                            ),
                            filters=azure_native.storage.ManagementPolicyFilterArgs(
                                blob_types=["blockBlob", "appendBlob"],
                                prefix_match=[
                                    internal_container_name.apply(lambda cn: f"{cn}/lag-reporter/")
                                ],
                            ),
                        ),
                    ),
                ],
            ),
            opts=child_opts,
        )

        self.register_outputs(
            {
                "account_name": self.storage_account.name,
                "access_key": self.access_key,
            }
        )

    @property
    def account_name(self) -> pulumi.Output[str]:
        return self.storage_account.name
