"""Pulumi Kubernetes Operator setup for Azure."""

import pulumi
import pulumi_azure_native as azure_native
import pulumi_kubernetes as k8s

from config.azure import AzureConfig

from .naming import key_vault_name


class PulumiOperator(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        k8s_provider: k8s.Provider,
        resource_group_name: pulumi.Input[str],
        resource_group_id: pulumi.Input[str],
        storage_account: azure_native.storage.StorageAccount,
        oidc_issuer_url: pulumi.Input[str],
        tenant_id: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        operator_namespace: str = "pulumi-kubernetes-operator",
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:PulumiOperator", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        self._resource_group_name = pulumi.Output.from_input(resource_group_name)
        self._resource_group_id = pulumi.Output.from_input(resource_group_id)
        self._storage_account = storage_account
        self._oidc_issuer_url = pulumi.Output.from_input(oidc_issuer_url)
        self._tenant_id = pulumi.Output.from_input(tenant_id)
        self._operator_namespace = operator_namespace
        child_opts = pulumi.ResourceOptions(parent=self)

        self._state_container = self._create_state_container(name, child_opts)
        self._key_vault = self._create_key_vault(name, child_opts)
        self._key_vault_key = self._create_key_vault_key(name, child_opts)
        self._identity = self._create_managed_identity(name, child_opts)
        self._create_role_assignments(name, child_opts)
        self._create_k8s_service_account(name, k8s_provider, child_opts)

        self._backend_url = self._storage_account.name.apply(
            lambda acct: f"azblob://pulumi-state?storage_account={acct}"
        )
        self._secrets_provider = pulumi.Output.all(
            self._key_vault.name,
        ).apply(lambda args: f"azurekeyvault://{args[0]}.vault.azure.net/keys/pulumi-key")
        self._identity_client_id = self._identity.client_id

        self.register_outputs(
            {
                "storage_account_name": self._storage_account.name,
                "key_vault_name": self._key_vault.name,
                "backend_url": self._backend_url,
                "secrets_provider": self._secrets_provider,
                "identity_client_id": self._identity_client_id,
            }
        )

    def _create_state_container(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> azure_native.storage.BlobContainer:
        return azure_native.storage.BlobContainer(
            f"{name}-state-container",
            account_name=self._storage_account.name,
            container_name="pulumi-state",
            resource_group_name=self._resource_group_name,
            opts=opts,
        )

    def _create_key_vault(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> azure_native.keyvault.Vault:
        vault_name = self._cell_name.apply(lambda cn: key_vault_name("pc", cn))

        return azure_native.keyvault.Vault(
            f"{name}-key-vault",
            vault_name=vault_name,
            resource_group_name=self._resource_group_name,
            location=self.config.region,
            properties=azure_native.keyvault.VaultPropertiesArgs(
                tenant_id=self._tenant_id,
                sku=azure_native.keyvault.SkuArgs(
                    family=azure_native.keyvault.SkuFamily.A,
                    name=azure_native.keyvault.SkuName.STANDARD,
                ),
                enable_rbac_authorization=True,
                soft_delete_retention_in_days=7,
            ),
            tags=self.config.tags(),
            opts=opts,
        )

    def _create_key_vault_key(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> azure_native.keyvault.Key:
        # retain_on_delete: vault deletion cascades to keys, so skip explicit
        # key deletion (which would require Key Vault Crypto Officer RBAC)
        return azure_native.keyvault.Key(
            f"{name}-pulumi-key",
            key_name="pulumi-key",
            vault_name=self._key_vault.name,
            resource_group_name=self._resource_group_name,
            properties=azure_native.keyvault.KeyPropertiesArgs(
                kty=azure_native.keyvault.JsonWebKeyType.RSA,
                key_size=2048,
            ),
            opts=pulumi.ResourceOptions(parent=self, retain_on_delete=True),
        )

    def _create_managed_identity(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> azure_native.managedidentity.UserAssignedIdentity:
        identity = azure_native.managedidentity.UserAssignedIdentity(
            f"{name}-operator-identity",
            resource_name_=self._cell_name.apply(lambda cn: f"pulumi-operator-{cn}"),
            resource_group_name=self._resource_group_name,
            location=self.config.region,
            tags=self.config.tags(),
            opts=opts,
        )

        # federated credential for workload identity
        azure_native.managedidentity.FederatedIdentityCredential(
            f"{name}-operator-federated-cred",
            federated_identity_credential_resource_name="pulumi-operator",
            resource_group_name=self._resource_group_name,
            resource_name_=identity.name,
            issuer=self._oidc_issuer_url,
            subject=f"system:serviceaccount:{self._operator_namespace}:pulumi-k8s-operator",
            audiences=["api://AzureADTokenExchange"],
            opts=opts,
        )

        return identity

    def _role_definition(self, role_id: str) -> str:
        return f"/subscriptions/{self.config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_id}"

    def _create_role_assignments(self, name: str, opts: pulumi.ResourceOptions):
        # storage blob data contributor scoped to state container
        azure_native.authorization.RoleAssignment(
            f"{name}-operator-storage-role",
            principal_id=self._identity.principal_id,
            principal_type=azure_native.authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=self._role_definition("ba92f5b4-2d11-453d-a403-e96b0029c9fe"),
            scope=self._state_container.id,
            opts=opts,
        )

        # key vault crypto user on key vault
        azure_native.authorization.RoleAssignment(
            f"{name}-operator-keyvault-role",
            principal_id=self._identity.principal_id,
            principal_type=azure_native.authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=self._role_definition("12338af0-0e69-4776-bea7-57ae8d297424"),
            scope=self._key_vault.id,
            opts=opts,
        )

        # contributor on resource group for AKS node pool management
        azure_native.authorization.RoleAssignment(
            f"{name}-operator-rg-contributor-role",
            principal_id=self._identity.principal_id,
            principal_type=azure_native.authorization.PrincipalType.SERVICE_PRINCIPAL,
            role_definition_id=self._role_definition("b24988ac-6180-42a0-ab88-20f7382dd24c"),
            scope=self._resource_group_id,
            opts=opts,
        )

    def _create_k8s_service_account(
        self,
        name: str,
        k8s_provider: k8s.Provider,
        opts: pulumi.ResourceOptions,
    ):
        ns = k8s.core.v1.Namespace(
            f"{name}-operator-ns",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=self._operator_namespace,
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
            ),
        )

        k8s.core.v1.ServiceAccount(
            f"{name}-operator-sa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="pulumi-kubernetes-operator",
                namespace=self._operator_namespace,
                annotations={
                    "azure.workload.identity/client-id": self._identity.client_id,
                },
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[ns],
            ),
        )

    @property
    def backend_url(self) -> pulumi.Output[str]:
        return self._backend_url

    @property
    def secrets_provider(self) -> pulumi.Output[str]:
        return self._secrets_provider

    @property
    def identity_client_id(self) -> pulumi.Output[str]:
        return self._identity_client_id

    @property
    def namespace(self) -> str:
        return self._operator_namespace
