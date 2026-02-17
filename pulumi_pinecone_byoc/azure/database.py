"""PostgreSQL Flexible Server infrastructure for Azure BYOC."""

import pulumi
import pulumi_azure_native.dbforpostgresql as dbforpostgresql
import pulumi_azure_native.network as network
import pulumi_random as random

from config.azure import AzureConfig


class _FlexibleServerClusterCompat:
    """Mimics RDS cluster interface for K8sSecrets compatibility."""

    def __init__(self, server: dbforpostgresql.Server):
        self._server = server

    @property
    def endpoint(self) -> pulumi.Output[str]:
        return self._server.fully_qualified_domain_name

    @property
    def reader_endpoint(self) -> pulumi.Output[str]:
        return self._server.fully_qualified_domain_name

    @property
    def port(self) -> pulumi.Output[int]:
        return pulumi.Output.from_input(5432)


class _FlexibleServerConfigCompat:
    """Mimics RDS db_config interface for K8sSecrets compatibility."""

    def __init__(self, username: str, db_name: str):
        self.username = username
        self.db_name = db_name


class FlexibleServerInstance:
    def __init__(
        self,
        server: dbforpostgresql.Server,
        password: random.RandomPassword,
        db_name: str,
        username: str,
    ):
        self.server = server
        self.password = password
        self.db_name = db_name
        self.username = username
        self.cluster = _FlexibleServerClusterCompat(server)
        self.db_config = _FlexibleServerConfigCompat(username, db_name)
        self._random_password = password

    @property
    def hostname(self) -> pulumi.Output[str]:
        return self.server.fully_qualified_domain_name

    @property
    def endpoint(self) -> pulumi.Output[str]:
        return self.server.fully_qualified_domain_name

    @property
    def port(self) -> int:
        return 5432

    @property
    def connection_string(self) -> pulumi.Output[str]:
        return pulumi.Output.all(
            self.server.fully_qualified_domain_name,
            self.username,
            self.password.result,
            self.db_name,
        ).apply(lambda args: f"postgresql://{args[1]}:{args[2]}@{args[0]}:5432/{args[3]}")


class Database(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        resource_group_name: pulumi.Input[str],
        vnet_id: pulumi.Output[str],
        delegated_subnet_id: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:Database", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        private_dns_zone = network.PrivateZone(
            f"{name}-private-dns-zone",
            location="global",
            private_zone_name="pinecone.postgres.database.azure.com",
            resource_group_name=resource_group_name,
            tags=config.tags(),
            opts=pulumi.ResourceOptions(parent=self),
        )

        network.VirtualNetworkLink(
            f"{name}-vnet-link",
            location="Global",
            private_zone_name=private_dns_zone.name,
            registration_enabled=False,
            resource_group_name=resource_group_name,
            virtual_network=network.SubResourceArgs(id=vnet_id),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[private_dns_zone]),
        )

        self._control_db = self._create_flexible_server(
            name=f"{name}-control-db",
            db_config=config.database.control_db,
            config=config,
            resource_group_name=resource_group_name,
            delegated_subnet_id=delegated_subnet_id,
            private_dns_zone_id=private_dns_zone.id,
        )

        self._system_db = self._create_flexible_server(
            name=f"{name}-system-db",
            db_config=config.database.system_db,
            config=config,
            resource_group_name=resource_group_name,
            delegated_subnet_id=delegated_subnet_id,
            private_dns_zone_id=private_dns_zone.id,
        )

        self.register_outputs(
            {
                "control_db_endpoint": self._control_db.endpoint,
                "system_db_endpoint": self._system_db.endpoint,
            }
        )

    def _create_flexible_server(
        self,
        name: str,
        db_config,
        config: AzureConfig,
        resource_group_name: pulumi.Input[str],
        delegated_subnet_id: pulumi.Output[str],
        private_dns_zone_id: pulumi.Output[str],
    ) -> FlexibleServerInstance:
        password = random.RandomPassword(
            f"{name}-password",
            length=32,
            special=False,
            opts=pulumi.ResourceOptions(parent=self),
        )

        server_name = self._cell_name.apply(lambda cn: f"{db_config.name}-{cn}")

        server = dbforpostgresql.Server(
            name,
            server_name=server_name,
            resource_group_name=resource_group_name,
            administrator_login=db_config.username,
            administrator_login_password=password.result,
            version="16",
            create_mode=dbforpostgresql.CreateMode.CREATE,
            sku=dbforpostgresql.SkuArgs(
                name=db_config.sku_name,
                tier=dbforpostgresql.SkuTier.GENERAL_PURPOSE,
            ),
            storage=dbforpostgresql.StorageArgs(
                storage_size_gb=512,
            ),
            high_availability=dbforpostgresql.HighAvailabilityArgs(
                mode=dbforpostgresql.HighAvailabilityMode.DISABLED,
            ),
            network=dbforpostgresql.NetworkArgs(
                delegated_subnet_resource_id=delegated_subnet_id,
                private_dns_zone_arm_resource_id=private_dns_zone_id,
            ),
            tags=config.tags(),
            opts=pulumi.ResourceOptions(
                parent=self,
                protect=config.database.deletion_protection,
                ignore_changes=["availabilityZone", "highAvailability", "network"],
            ),
        )

        dbforpostgresql.Database(
            f"{name}-db",
            database_name=db_config.db_name,
            server_name=server.name,
            resource_group_name=resource_group_name,
            charset="UTF8",
            opts=pulumi.ResourceOptions(parent=self, depends_on=[server]),
        )

        return FlexibleServerInstance(
            server=server,
            password=password,
            db_name=db_config.db_name,
            username=db_config.username,
        )

    @property
    def control_db(self) -> FlexibleServerInstance:
        return self._control_db

    @property
    def system_db(self) -> FlexibleServerInstance:
        return self._system_db
