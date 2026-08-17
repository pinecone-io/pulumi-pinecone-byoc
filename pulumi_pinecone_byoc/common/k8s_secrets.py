"""
Shared k8s secrets for pinecone services.
"""

import base64

import pulumi
import pulumi_kubernetes as k8s


def b64(data: pulumi.Input[str]) -> pulumi.Output[str]:
    return pulumi.Output.from_input(data).apply(
        lambda v: base64.b64encode(str(v).encode("utf-8")).decode("utf-8")
    )


class K8sSecrets(pulumi.ComponentResource):
    cpgw_api_key: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        cpgw_api_key: pulumi.Input[str],
        gcps_api_key: pulumi.Input[str] | None = None,
        dd_api_key: pulumi.Input[str] | None = None,
        azure_storage_access_key: pulumi.Input[str] | None = None,
        storage_integration_credentials: dict[str, pulumi.Input[str]] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:K8sSecrets", name, None, opts)

        self.cpgw_api_key = pulumi.Output.secret(cpgw_api_key)

        self.namespace = k8s.core.v1.Namespace(
            f"{name}-external-secrets-ns",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="external-secrets",
                labels={
                    "kubernetes.io/metadata.name": "external-secrets",
                    "name": "external-secrets",
                },
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                delete_before_replace=True,
            ),
        )

        ns_opts = pulumi.ResourceOptions(
            parent=self,
            provider=k8s_provider,
            depends_on=[self.namespace],
        )

        k8s.core.v1.Secret(
            f"{name}-cpgw-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="cpgw-credentials",
                namespace="external-secrets",
            ),
            data={
                "api-key": b64(self.cpgw_api_key),
            },
            type="Opaque",
            opts=ns_opts,
        )

        if gcps_api_key is not None:
            k8s.core.v1.Secret(
                f"{name}-gcps-api-key",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="gcps-api-key",
                    namespace="external-secrets",
                ),
                data={
                    "api-key": b64(gcps_api_key),
                },
                type="Opaque",
                opts=ns_opts,
            )

        if dd_api_key is not None:
            k8s.core.v1.Secret(
                f"{name}-datadog-api-key",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="datadog-api-key",
                    namespace="external-secrets",
                ),
                data={
                    "api-key": b64(dd_api_key),
                },
                type="Opaque",
                opts=ns_opts,
            )

        if azure_storage_access_key is not None:
            k8s.core.v1.Secret(
                f"{name}-azure-storage-key",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="azure-storage-account-access-key",
                    namespace="external-secrets",
                ),
                data={
                    "key": b64(azure_storage_access_key),
                },
                type="Opaque",
                opts=ns_opts,
            )

        if storage_integration_credentials is not None:
            k8s.core.v1.Secret(
                f"{name}-storage-integration-credentials",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="storage-integration-credentials",
                    namespace="external-secrets",
                ),
                data={k: b64(v) for k, v in storage_integration_credentials.items()},
                type="Opaque",
                opts=ns_opts,
            )

        self.register_outputs(
            {
                "cpgw_api_key": self.cpgw_api_key,
                "namespace": self.namespace.metadata.name,
            }
        )
