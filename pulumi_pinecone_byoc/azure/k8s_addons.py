"""K8s addons for Azure infrastructure."""

import json

import pulumi
import pulumi_kubernetes as k8s
from pulumi_azure_native import authorization, managedidentity

from config.azure import AzureConfig

# built-in Azure role definition IDs
DNS_ZONE_CONTRIBUTOR_ROLE = "befefa01-2a29-4197-83a8-272ff33ce314"


class K8sAddons(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        k8s_provider: pulumi.ProviderResource,
        oidc_issuer_url: pulumi.Output[str],
        resource_group_name: pulumi.Input[str],
        dns_zone_id: pulumi.Input[str],
        cell_name: pulumi.Input[str],
        tenant_id: str,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:K8sAddons", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)
        self._rg_name = pulumi.Output.from_input(resource_group_name)
        child_opts = pulumi.ResourceOptions(parent=self)
        k8s_opts = pulumi.ResourceOptions(parent=self, provider=k8s_provider)

        # gloo-system namespace
        self.gloo_namespace = k8s.core.v1.Namespace(
            f"{name}-gloo-system",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="gloo-system",
                labels={
                    "kubernetes.io/metadata.name": "gloo-system",
                    "name": "gloo-system",
                },
            ),
            opts=k8s_opts,
        )

        # external-dns: managed identity + federated credential + k8s service account
        self._dns_identity = managedidentity.UserAssignedIdentity(
            f"{name}-external-dns-identity",
            resource_group_name=self._rg_name,
            resource_name_=self._cell_name.apply(lambda cn: f"external-dns-{cn}"),
            tags=config.tags(),
            opts=child_opts,
        )

        managedidentity.FederatedIdentityCredential(
            f"{name}-external-dns-fic",
            resource_group_name=self._rg_name,
            resource_name_=self._dns_identity.name,
            federated_identity_credential_resource_name=self._cell_name.apply(
                lambda cn: f"external-dns-fic-{cn}"
            ),
            audiences=["api://AzureADTokenExchange"],
            issuer=oidc_issuer_url,
            subject="system:serviceaccount:gloo-system:external-dns",
            opts=child_opts,
        )

        # dns zone contributor role on the DNS zone resource group
        authorization.RoleAssignment(
            f"{name}-external-dns-role",
            principal_id=self._dns_identity.principal_id,
            principal_type="ServicePrincipal",
            role_definition_id=pulumi.Output.from_input(config.subscription_id).apply(
                lambda sid: (
                    f"/subscriptions/{sid}/providers/Microsoft.Authorization/roleDefinitions/{DNS_ZONE_CONTRIBUTOR_ROLE}"
                )
            ),
            scope=dns_zone_id,
            opts=child_opts,
        )

        k8s.core.v1.ServiceAccount(
            f"{name}-external-dns-sa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="external-dns",
                namespace="gloo-system",
                labels={"azure.workload.identity/use": "true"},
                annotations={
                    "azure.workload.identity/client-id": self._dns_identity.client_id,
                },
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[self.gloo_namespace],
            ),
        )

        # external-dns azure.json config secret (consumed by netstack helm chart)
        k8s.core.v1.Secret(
            f"{name}-external-dns-azure",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="external-dns-azure",
                namespace="gloo-system",
                annotations={
                    "pulumi.com/patchForce": "true",
                },
            ),
            string_data={
                "azure.json": self._rg_name.apply(
                    lambda rg: json.dumps(
                        {
                            "tenantId": tenant_id,
                            "subscriptionId": config.subscription_id,
                            "resourceGroup": rg,
                            "useWorkloadIdentityExtension": True,
                        }
                    )
                ),
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[self.gloo_namespace],
            ),
        )

        # cert-manager: managed identity + federated credential for ACME DNS01 challenges
        self._certmanager_identity = managedidentity.UserAssignedIdentity(
            f"{name}-certmanager-identity",
            resource_group_name=self._rg_name,
            resource_name_=self._cell_name.apply(lambda cn: f"certmanager-{cn}"),
            tags=config.tags(),
            opts=child_opts,
        )

        managedidentity.FederatedIdentityCredential(
            f"{name}-certmanager-fic",
            resource_group_name=self._rg_name,
            resource_name_=self._certmanager_identity.name,
            federated_identity_credential_resource_name=self._cell_name.apply(
                lambda cn: f"certmanager-fic-{cn}"
            ),
            audiences=["api://AzureADTokenExchange"],
            issuer=oidc_issuer_url,
            subject="system:serviceaccount:gloo-system:certmanager-certgen",
            opts=child_opts,
        )

        authorization.RoleAssignment(
            f"{name}-certmanager-dns-role",
            principal_id=self._certmanager_identity.principal_id,
            principal_type="ServicePrincipal",
            role_definition_id=pulumi.Output.from_input(config.subscription_id).apply(
                lambda sid: (
                    f"/subscriptions/{sid}/providers/Microsoft.Authorization/roleDefinitions/{DNS_ZONE_CONTRIBUTOR_ROLE}"
                )
            ),
            scope=dns_zone_id,
            opts=child_opts,
        )

        # prometheus: managed identity + federated credential for AMP remote write
        self._prometheus_identity = managedidentity.UserAssignedIdentity(
            f"{name}-prometheus-identity",
            resource_group_name=self._rg_name,
            resource_name_=self._cell_name.apply(lambda cn: f"prometheus-{cn}"),
            tags=config.tags(),
            opts=child_opts,
        )

        managedidentity.FederatedIdentityCredential(
            f"{name}-prometheus-fic",
            resource_group_name=self._rg_name,
            resource_name_=self._prometheus_identity.name,
            federated_identity_credential_resource_name=self._cell_name.apply(
                lambda cn: f"prometheus-fic-{cn}"
            ),
            audiences=["api://AzureADTokenExchange"],
            issuer=oidc_issuer_url,
            subject="system:serviceaccount:prometheus:amp-iamproxy-ingest-service-account",
            opts=child_opts,
        )

        self.register_outputs(
            {
                "gloo_namespace": self.gloo_namespace.metadata.name,
                "dns_identity_client_id": self._dns_identity.client_id,
                "certmanager_identity_client_id": self._certmanager_identity.client_id,
                "prometheus_identity_client_id": self._prometheus_identity.client_id,
            }
        )

    @property
    def dns_identity_client_id(self) -> pulumi.Output[str]:
        return self._dns_identity.client_id

    @property
    def certmanager_identity_client_id(self) -> pulumi.Output[str]:
        return self._certmanager_identity.client_id

    @property
    def prometheus_identity_client_id(self) -> pulumi.Output[str]:
        return self._prometheus_identity.client_id
