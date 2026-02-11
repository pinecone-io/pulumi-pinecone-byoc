"""AlloyDB cluster infrastructure for PostgreSQL databases."""

import pulumi
import pulumi_gcp as gcp
import pulumi_random as random

from config.gcp import GCPConfig


class _AlloyDBClusterCompat:
    """Mimics RDS cluster interface for K8sSecrets compatibility."""

    def __init__(self, instance: gcp.alloydb.Instance):
        self._instance = instance

    @property
    def endpoint(self) -> pulumi.Output[str]:
        return self._instance.ip_address

    @property
    def reader_endpoint(self) -> pulumi.Output[str]:
        return self._instance.ip_address

    @property
    def port(self) -> pulumi.Output[int]:
        return pulumi.Output.from_input(5432)


class _AlloyDBConfigCompat:
    """Mimics RDS db_config interface for K8sSecrets compatibility."""

    def __init__(self, username: str, db_name: str):
        self.username = username
        self.db_name = db_name


class AlloyDBInstance:
    def __init__(
        self,
        alloydb_cluster: gcp.alloydb.Cluster,
        instance: gcp.alloydb.Instance,
        password: random.RandomPassword,
        db_name: str,
        username: str,
    ):
        self._alloydb_cluster = alloydb_cluster
        self.instance = instance
        self.password = password
        self.db_name = db_name
        self.username = username
        self.cluster = _AlloyDBClusterCompat(instance)
        self.db_config = _AlloyDBConfigCompat(username, db_name)
        self._random_password = password

    @property
    def endpoint(self) -> pulumi.Output[str]:
        return self.instance.ip_address

    @property
    def port(self) -> int:
        return 5432

    @property
    def connection_string(self) -> pulumi.Output[str]:
        return pulumi.Output.all(
            self.instance.ip_address, self.username, self.password.result, self.db_name
        ).apply(lambda args: f"postgresql://{args[1]}:{args[2]}@{args[0]}:5432/{args[3]}")


class AlloyDB(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: GCPConfig,
        network_id: pulumi.Output[str],
        private_ip_range_name: pulumi.Output[str],
        private_connection: gcp.servicenetworking.Connection,
        cell_name: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:AlloyDB", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        self._control_db = self._create_alloydb_cluster(
            name=f"{name}-control-db",
            db_config=config.database.control_db,
            config=config,
            network_id=network_id,
            private_ip_range_name=private_ip_range_name,
            private_connection=private_connection,
        )

        self._system_db = self._create_alloydb_cluster(
            name=f"{name}-system-db",
            db_config=config.database.system_db,
            config=config,
            network_id=network_id,
            private_ip_range_name=private_ip_range_name,
            private_connection=private_connection,
        )

        self.register_outputs(
            {
                "control_db_endpoint": self._control_db.endpoint,
                "system_db_endpoint": self._system_db.endpoint,
            }
        )

    def _create_alloydb_cluster(
        self,
        name: str,
        db_config,
        config: GCPConfig,
        network_id: pulumi.Output[str],
        private_ip_range_name: pulumi.Output[str],
        private_connection: gcp.servicenetworking.Connection,
    ) -> AlloyDBInstance:
        cluster_id = self._cell_name.apply(lambda cn: f"{db_config.name}-{cn}")

        password = random.RandomPassword(
            f"{name}-password",
            length=32,
            special=False,
            opts=pulumi.ResourceOptions(parent=self),
        )

        cluster = gcp.alloydb.Cluster(
            name,
            cluster_id=cluster_id,
            location=config.region,
            network_config=gcp.alloydb.ClusterNetworkConfigArgs(
                network=network_id,
                allocated_ip_range=private_ip_range_name,
            ),
            project=config.project,
            initial_user=gcp.alloydb.ClusterInitialUserArgs(
                user=db_config.username,
                password=password.result,
            ),
            deletion_policy="FORCE" if not config.database.deletion_protection else "DEFAULT",
            labels=config.labels(),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[private_connection]),
        )

        instance_id = self._cell_name.apply(lambda cn: f"{db_config.name}-{cn}-instance")
        instance = gcp.alloydb.Instance(
            f"{name}-instance",
            instance_id=instance_id,
            instance_type="PRIMARY",
            cluster=cluster.name,
            machine_config=gcp.alloydb.InstanceMachineConfigArgs(cpu_count=db_config.cpu_count),
            availability_type="REGIONAL" if config.database.deletion_protection else "ZONAL",
            labels=config.labels(),
            database_flags={
                "max_connections": str(self._calculate_max_connections(db_config.cpu_count)),
            },
            opts=pulumi.ResourceOptions(parent=self, depends_on=[cluster]),
        )

        secret_id = self._cell_name.apply(lambda cn: f"{db_config.name}-{cn}-credentials")
        secret = gcp.secretmanager.Secret(
            f"{name}-secret",
            secret_id=secret_id,
            replication=gcp.secretmanager.SecretReplicationArgs(
                auto=gcp.secretmanager.SecretReplicationAutoArgs()
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        secret_value = pulumi.Output.all(
            instance.ip_address, db_config.username, password.result, db_config.db_name
        ).apply(
            lambda args: (
                f'{{"host": "{args[0]}", "port": 5432, "username": "{args[1]}", "password": "{args[2]}", "database": "{args[3]}"}}'
            )
        )

        gcp.secretmanager.SecretVersion(
            f"{name}-secret-version",
            secret=secret.id,
            secret_data=secret_value,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[secret]),
        )

        return AlloyDBInstance(
            alloydb_cluster=cluster,
            instance=instance,
            password=password,
            db_name=db_config.db_name,
            username=db_config.username,
        )

    def _calculate_max_connections(self, cpu_count: int) -> int:
        if cpu_count >= 8:
            return 4000
        if cpu_count >= 4:
            return 2000
        return 1000

    @property
    def control_db(self) -> AlloyDBInstance:
        return self._control_db

    @property
    def system_db(self) -> AlloyDBInstance:
        return self._system_db
