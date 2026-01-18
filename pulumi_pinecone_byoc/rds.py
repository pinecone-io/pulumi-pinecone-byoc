"""
RDS component for Pinecone BYOC infrastructure.

Creates Aurora PostgreSQL clusters for control-db and system-db.
"""

from typing import Optional
import json

import pulumi
import pulumi_aws as aws
import pulumi_random as random

from config import Config, DatabaseInstanceConfig
from .vpc import VPC


class RDSInstance(pulumi.ComponentResource):
    """
    Creates a single Aurora PostgreSQL cluster with one instance.

    Used for both control-db and system-db.
    """

    def __init__(
        self,
        name: str,
        config: Config,
        db_config: DatabaseInstanceConfig,
        vpc: VPC,
        security_group_id: pulumi.Output[str],
        subnet_group_name: pulumi.Output[str],
        kms_key_arn: Optional[pulumi.Output[str]] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:RDSInstance", name, None, opts)

        self.config = config
        self.db_config = db_config
        child_opts = pulumi.ResourceOptions(parent=self)

        # Generate password using pulumi_random (persists across updates)
        self._random_password = random.RandomPassword(
            f"{name}-random-password",
            length=32,
            special=False,
            opts=child_opts,
        )

        self.master_password = aws.secretsmanager.Secret(
            f"{name}-master-password",
            name=f"{config.resource_prefix}/{db_config.name}/master-password",
            tags=config.tags(Name=f"{config.resource_prefix}-{db_config.name}-master-password"),
            opts=child_opts,
        )

        random_password = aws.secretsmanager.SecretVersion(
            f"{name}-master-password-version",
            secret_id=self.master_password.id,
            secret_string=self._random_password.result,
            opts=child_opts,
        )

        # Cluster parameter group
        cluster_parameter_group = aws.rds.ClusterParameterGroup(
            f"{name}-cluster-params",
            family="aurora-postgresql15",
            name=f"{config.resource_prefix}-{db_config.name}-params",
            parameters=[
                aws.rds.ClusterParameterGroupParameterArgs(
                    name="log_statement",
                    value="ddl",
                ),
                aws.rds.ClusterParameterGroupParameterArgs(
                    name="log_min_duration_statement",
                    value="1000",
                ),
            ],
            tags=config.tags(Name=f"{config.resource_prefix}-{db_config.name}-params"),
            opts=child_opts,
        )

        # Create Aurora cluster
        # Note: Use password.result directly (like satellites/aws_exdb.py) instead of
        # going through SecretVersion to avoid potential timing/resolution issues
        cluster_args = {
            "cluster_identifier": f"{config.resource_prefix}-{db_config.name}",
            "engine": "aurora-postgresql",
            "engine_mode": "provisioned",
            "engine_version": db_config.engine_version,
            "database_name": db_config.db_name,
            "master_username": db_config.username,
            "master_password": self._random_password.result,
            "db_subnet_group_name": subnet_group_name,
            "vpc_security_group_ids": [security_group_id],
            "db_cluster_parameter_group_name": cluster_parameter_group.name,
            "backup_retention_period": db_config.backup_retention_days,
            "preferred_backup_window": "03:00-04:00",
            "preferred_maintenance_window": "sun:04:00-sun:05:00",
            "deletion_protection": db_config.deletion_protection,
            "skip_final_snapshot": not config.is_production,
            "final_snapshot_identifier": f"{config.resource_prefix}-{db_config.name}-final"
            if config.is_production
            else None,
            "tags": config.tags(Name=f"{config.resource_prefix}-{db_config.name}"),
        }

        if kms_key_arn:
            cluster_args["storage_encrypted"] = True
            cluster_args["kms_key_id"] = kms_key_arn

        self.cluster = aws.rds.Cluster(
            f"{name}-cluster",
            **cluster_args,
            opts=child_opts,
        )

        # Create single instance (db.r8g.large)
        self.instance = aws.rds.ClusterInstance(
            f"{name}-instance",
            identifier=f"{config.resource_prefix}-{db_config.name}-instance-0",
            cluster_identifier=self.cluster.id,
            instance_class=db_config.instance_class,
            engine=self.cluster.engine,
            engine_version=self.cluster.engine_version,
            publicly_accessible=False,
            db_subnet_group_name=subnet_group_name,
            performance_insights_enabled=True,
            performance_insights_retention_period=7,
            auto_minor_version_upgrade=False,
            tags=config.tags(Name=f"{config.resource_prefix}-{db_config.name}-instance-0"),
            opts=child_opts,
        )

        # Store connection info in Secrets Manager
        self.connection_secret = aws.secretsmanager.Secret(
            f"{name}-connection",
            name=f"{config.resource_prefix}/{db_config.name}/connection",
            tags=config.tags(Name=f"{config.resource_prefix}-{db_config.name}-connection"),
            opts=child_opts,
        )

        connection_info = pulumi.Output.all(
            host=self.cluster.endpoint,
            port=self.cluster.port,
            database=self.cluster.database_name,
            username=self.cluster.master_username,
            password=random_password.secret_string,
        ).apply(
            lambda args: json.dumps(
                {
                    "host": args["host"],
                    "port": args["port"],
                    "database": args["database"],
                    "username": args["username"],
                    "password": args["password"],
                }
            )
        )

        aws.secretsmanager.SecretVersion(
            f"{name}-connection-version",
            secret_id=self.connection_secret.id,
            secret_string=connection_info,
            opts=child_opts,
        )

        self.register_outputs(
            {
                "cluster_endpoint": self.cluster.endpoint,
                "cluster_reader_endpoint": self.cluster.reader_endpoint,
                "connection_secret_arn": self.connection_secret.arn,
            }
        )

    @property
    def endpoint(self) -> pulumi.Output[str]:
        return self.cluster.endpoint

    @property
    def reader_endpoint(self) -> pulumi.Output[str]:
        return self.cluster.reader_endpoint

    @property
    def port(self) -> pulumi.Output[int]:
        return self.cluster.port

    @property
    def connection_secret_arn(self) -> pulumi.Output[str]:
        return self.connection_secret.arn


class RDS(pulumi.ComponentResource):
    """
    Creates Aurora PostgreSQL databases for Pinecone BYOC:
    - control-db: Controller database (1 shard)
    - system-db: System database

    Both use db.r8g.large instance class.
    """

    def __init__(
        self,
        name: str,
        config: Config,
        vpc: VPC,
        kms_key_arn: Optional[pulumi.Output[str]] = None,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:RDS", name, None, opts)

        self.config = config
        child_opts = pulumi.ResourceOptions(parent=self)

        # Shared subnet group for all databases
        self.subnet_group = aws.rds.SubnetGroup(
            f"{name}-subnet-group",
            name=f"{config.resource_prefix}-db",
            subnet_ids=vpc.private_subnet_ids,
            tags=config.tags(Name=f"{config.resource_prefix}-db-subnet-group"),
            opts=child_opts,
        )

        # Shared security group for all databases
        self.security_group = aws.ec2.SecurityGroup(
            f"{name}-sg",
            vpc_id=vpc.vpc_id,
            description=f"Security group for {config.resource_prefix} RDS",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=5432,
                    to_port=5432,
                    cidr_blocks=[config.vpc_cidr],
                    description="PostgreSQL from VPC",
                ),
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags=config.tags(Name=f"{config.resource_prefix}-rds-sg"),
            opts=child_opts,
        )

        # Create control-db (1 shard)
        self._control_db = RDSInstance(
            f"{name}-control-db",
            config=config,
            db_config=config.database.control_db,
            vpc=vpc,
            security_group_id=self.security_group.id,
            subnet_group_name=self.subnet_group.name,
            kms_key_arn=kms_key_arn,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self.subnet_group, self.security_group]),
        )

        # Create system-db
        self._system_db = RDSInstance(
            f"{name}-system-db",
            config=config,
            db_config=config.database.system_db,
            vpc=vpc,
            security_group_id=self.security_group.id,
            subnet_group_name=self.subnet_group.name,
            kms_key_arn=kms_key_arn,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self.subnet_group, self.security_group]),
        )

        self.register_outputs(
            {
                "control_db_endpoint": self._control_db.endpoint,
                "control_db_connection_secret_arn": self._control_db.connection_secret_arn,
                "system_db_endpoint": self._system_db.endpoint,
                "system_db_connection_secret_arn": self._system_db.connection_secret_arn,
            }
        )

    @property
    def control_db(self) -> RDSInstance:
        """The control database instance."""
        return self._control_db

    @property
    def system_db(self) -> RDSInstance:
        """The system database instance."""
        return self._system_db

    @property
    def endpoint(self) -> pulumi.Output[str]:
        """Control DB endpoint (primary)."""
        return self._control_db.endpoint

    @property
    def connection_secret_arn(self) -> pulumi.Output[str]:
        """Control DB connection secret ARN (primary)."""
        return self._control_db.connection_secret_arn
