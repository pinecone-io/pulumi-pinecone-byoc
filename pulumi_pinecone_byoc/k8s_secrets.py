"""
Shared k8s secrets for pinecone services.
"""

import base64
import json
from typing import Optional, TYPE_CHECKING

import pulumi
import pulumi_kubernetes as k8s

if TYPE_CHECKING:
    from .rds import RDSInstance


def b64(data: str | pulumi.Output[str]) -> pulumi.Output[str]:
    return pulumi.Output.from_input(data).apply(
        lambda v: base64.b64encode(str(v).encode("utf-8")).decode("utf-8")
    )


def postgres_url(
    host: str, port: int, username: str, password: str, db_name: str
) -> str:
    return f"postgres://{username}:{password}@{host}:{port}/{db_name}"


class K8sSecrets(pulumi.ComponentResource):
    cpgw_api_key: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        cpgw_api_key: pulumi.Input[str],
        gcps_api_key: Optional[pulumi.Input[str]] = None,
        dd_api_key: Optional[pulumi.Input[str]] = None,
        control_db: Optional["RDSInstance"] = None,
        system_db: Optional["RDSInstance"] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
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

        # cpgw credentials - the actual api key for cpgw auth
        k8s.core.v1.Secret(
            f"{name}-cpgw-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="cpgw-credentials",
                namespace="external-secrets",
            ),
            data={
                "api-key": self.cpgw_api_key.apply(b64),
            },
            type="Opaque",
            opts=ns_opts,
        )

        # gcps api key (for sli-checkers)
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

        # datadog api key (from cpgw)
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

        # Create tooling namespace and database secrets if RDS is provided
        if control_db is not None and system_db is not None:
            self._create_db_secrets(name, k8s_provider, control_db, system_db)

        self.register_outputs(
            {
                "cpgw_api_key": self.cpgw_api_key,
                "namespace": self.namespace.metadata.name,
            }
        )

    def _create_db_secrets(
        self,
        name: str,
        k8s_provider: pulumi.ProviderResource,
        control_db: "RDSInstance",
        system_db: "RDSInstance",
    ):
        """Create database secrets in external-secrets namespace for ClusterExternalSecrets."""
        ns_opts = pulumi.ResourceOptions(
            parent=self,
            provider=k8s_provider,
            depends_on=[self.namespace],
        )

        # helper to build DB credentials dict with simple keys (for external secrets)
        def build_db_credentials(
            db: "RDSInstance",
        ) -> dict[str, pulumi.Output[str] | str]:
            url = pulumi.Output.all(
                host=db.cluster.endpoint,
                port=db.cluster.port,
                username=db.db_config.username,
                password=db._random_password.result,
                db_name=db.db_config.db_name,
            ).apply(lambda args: postgres_url(**args))

            readonly_url = pulumi.Output.all(
                host=db.cluster.reader_endpoint,
                port=db.cluster.port,
                username=db.db_config.username,
                password=db._random_password.result,
                db_name=db.db_config.db_name,
            ).apply(lambda args: postgres_url(**args))

            return {
                "url": url,
                "readonly_url": readonly_url,
                "host": db.cluster.endpoint,
                "readonly_host": db.cluster.reader_endpoint,
                "port": db.cluster.port.apply(str),
                "username": db.db_config.username,
                "password": db._random_password.result,
                "dbname": db.db_config.db_name,
            }

        # exdb-control-db-credentials in external-secrets namespace
        control_creds = build_db_credentials(control_db)
        k8s.core.v1.Secret(
            f"{name}-exdb-control-db-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="exdb-control-db-credentials",
                namespace="external-secrets",
            ),
            data={k: b64(v) for k, v in control_creds.items()},
            type="Opaque",
            opts=ns_opts,
        )

        # exdb-system-db-credentials in external-secrets namespace
        system_creds = build_db_credentials(system_db)
        k8s.core.v1.Secret(
            f"{name}-exdb-system-db-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="exdb-system-db-credentials",
                namespace="external-secrets",
            ),
            data={k: b64(v) for k, v in system_creds.items()},
            type="Opaque",
            opts=ns_opts,
        )

        # exdb-data-db-credentials - for BYOC, data is in control-db
        k8s.core.v1.Secret(
            f"{name}-exdb-data-db-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="exdb-data-db-credentials",
                namespace="external-secrets",
            ),
            data={k: b64(v) for k, v in control_creds.items()},
            type="Opaque",
            opts=ns_opts,
        )

        # exdb-all-credentials - JSON with all shard credentials
        # for BYOC we have control-1 and system shards
        def build_shards_json(
            control_url: str,
            control_readonly_url: str,
            control_host: str,
            control_readonly_host: str,
            control_port: int,
            control_password: str,
            system_url: str,
            system_readonly_url: str,
            system_host: str,
            system_readonly_host: str,
            system_port: int,
            system_password: str,
        ) -> str:
            shards = {
                "control-1": {
                    "url": control_url,
                    "readonly_url": control_readonly_url,
                    "host": control_host,
                    "readonly_host": control_readonly_host,
                    "port": control_port,
                    "username": control_db.db_config.username,
                    "password": control_password,
                    "dbname": control_db.db_config.db_name,
                },
                "system": {
                    "url": system_url,
                    "readonly_url": system_readonly_url,
                    "host": system_host,
                    "readonly_host": system_readonly_host,
                    "port": system_port,
                    "username": system_db.db_config.username,
                    "password": system_password,
                    "dbname": system_db.db_config.db_name,
                },
            }
            return json.dumps(shards)

        shards_json = pulumi.Output.all(
            control_url=control_creds["url"],
            control_readonly_url=control_creds["readonly_url"],
            control_host=control_db.cluster.endpoint,
            control_readonly_host=control_db.cluster.reader_endpoint,
            control_port=control_db.cluster.port,
            control_password=control_db._random_password.result,
            system_url=system_creds["url"],
            system_readonly_url=system_creds["readonly_url"],
            system_host=system_db.cluster.endpoint,
            system_readonly_host=system_db.cluster.reader_endpoint,
            system_port=system_db.cluster.port,
            system_password=system_db._random_password.result,
        ).apply(lambda args: build_shards_json(**args))

        k8s.core.v1.Secret(
            f"{name}-exdb-all-credentials",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="exdb-all-credentials",
                namespace="external-secrets",
            ),
            data={
                "shards": b64(shards_json),
            },
            type="Opaque",
            opts=ns_opts,
        )
