"""
VPC component for Pinecone BYOC infrastructure.

Creates a production-ready VPC with public and private subnets across multiple AZs.
"""

import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig

from . import vpc_perms, vpc_route, vpc_subnet


class VPC(pulumi.ComponentResource):
    """
    Creates a VPC with:
    - Public subnets (one per AZ) for load balancers and NAT gateways
    - Private subnets (one per AZ) for EKS nodes and RDS
    - NAT gateways for private subnet internet access
    - Internet gateway for public subnet internet access
    """

    def __init__(
        self,
        name: str,
        config: AWSConfig,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:VPC", name, None, opts)

        self.config = config

        if config.existing_vpc_id:
            self._create_subnets_in_existing_vpc(name, config, config.existing_vpc_id)
        else:
            self._create(name, config)

    def _create(self, name: str, config: AWSConfig) -> None:
        vpc_subnet.validate_vpc_cidr(config.vpc_cidr)
        if len(config.availability_zones) > 3:
            raise ValueError(
                f"Maximum 3 AZs supported (got {len(config.availability_zones)}). "
                "Subnet layout does not fit more than 3 AZs in a /16 VPC."
            )
        child_opts = pulumi.ResourceOptions(parent=self)

        self.vpc = aws.ec2.Vpc(
            f"{name}",
            cidr_block=config.vpc_cidr,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            tags=config.tags(Name=f"{config.resource_prefix}-vpc"),
            opts=child_opts,
        )

        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            tags=config.tags(Name=f"{config.resource_prefix}-igw"),
            opts=child_opts,
        )

        self.public_subnets: list[aws.ec2.Subnet] = []
        self.private_subnets: list[aws.ec2.Subnet] = []
        self.nat_gateways: list[aws.ec2.NatGateway] = []

        for i, az in enumerate(config.availability_zones):
            public_subnet = vpc_subnet.create(
                name,
                config,
                self.vpc.id,
                az,
                i,
                is_public=True,
                opts=child_opts,
                map_public_ip=True,
            )
            self.public_subnets.append(public_subnet)

            eip = aws.ec2.Eip(
                f"{name}-eip-{az}",
                domain="vpc",
                tags=config.tags(Name=f"{config.resource_prefix}-nat-{az}"),
                opts=child_opts,
            )

            nat = aws.ec2.NatGateway(
                f"{name}-nat-{az}",
                allocation_id=eip.id,
                subnet_id=public_subnet.id,
                tags=config.tags(Name=f"{config.resource_prefix}-nat-{az}"),
                opts=pulumi.ResourceOptions(parent=self, depends_on=[self.igw]),
            )
            self.nat_gateways.append(nat)

            private_subnet = vpc_subnet.create(
                name,
                config,
                self.vpc.id,
                az,
                i,
                is_public=False,
                opts=child_opts,
            )
            self.private_subnets.append(private_subnet)

        self._create_route_tables(name, child_opts)

        self._create_lb_backend_sg(name, config, self.vpc.id, child_opts)

        self._register_outputs(self.vpc.id)

    def _create_subnets_in_existing_vpc(self, name: str, config: AWSConfig, vpc_id: str) -> None:
        vpc = aws.ec2.get_vpc(id=vpc_id)
        vpc_subnet.validate_vpc_cidr(config.vpc_cidr)
        if len(config.availability_zones) > 3:
            raise ValueError(
                f"Maximum 3 AZs supported (got {len(config.availability_zones)}). "
                "Subnet layout does not fit more than 3 AZs in the range we associate."
            )
        vpc_subnet.validate_range_is_free(config, vpc_id)

        child_opts = pulumi.ResourceOptions(parent=self)

        ours = ipaddress.IPv4Network(config.vpc_cidr)
        theirs = [
            ipaddress.IPv4Network(a.cidr_block)
            for a in vpc.cidr_block_associations
            if a.cidr_block != config.vpc_cidr
        ]
        covered = ours.subnet_of(ipaddress.IPv4Network(vpc.cidr_block)) or any(
            ours.subnet_of(theirs_net) for theirs_net in theirs
        )
        subnet_deps: list[pulumi.Resource] = []
        if not covered:
            self.cidr_association = aws.ec2.VpcIpv4CidrBlockAssociation(
                f"{name}-cidr",
                vpc_id=vpc_id,
                cidr_block=config.vpc_cidr,
                opts=child_opts,
            )
            subnet_deps = [self.cidr_association]

        route_tables = config.existing_route_table_ids or {}
        missing = [az for az in config.availability_zones if az not in route_tables]
        if route_tables and missing:
            raise ValueError(
                f"existing_route_table_ids is missing a route table for {', '.join(missing)}. "
                "Give one per availability zone, or none at all to detect the route table "
                "their own subnets in that zone egress through."
            )
        if not route_tables:
            route_tables = vpc_route.detect(vpc_id, config.availability_zones)

        vpc_perms.warn(config, vpc_id, route_tables)

        self.private_subnets: list[aws.ec2.Subnet] = []
        self.private_route_table_associations: list[aws.ec2.RouteTableAssociation] = []
        for i, az in enumerate(config.availability_zones):
            private_subnet = vpc_subnet.create(
                name,
                config,
                vpc_id,
                az,
                i,
                is_public=False,
                opts=pulumi.ResourceOptions(parent=self, depends_on=subnet_deps),
            )
            self.private_subnets.append(private_subnet)

            if az in route_tables:
                self.private_route_table_associations.append(
                    aws.ec2.RouteTableAssociation(
                        f"{name}-private-rta-{az}",
                        subnet_id=private_subnet.id,
                        route_table_id=route_tables[az],
                        opts=child_opts,
                    )
                )

        self.public_subnets: list[aws.ec2.Subnet] = []
        if config.public_access:
            self._create_public_subnets_in_existing_vpc(
                name, config, vpc_id, subnet_deps, child_opts
            )

        self._create_lb_backend_sg(name, config, vpc_id, child_opts)

        self._register_outputs(vpc_id)

    def _create_public_subnets_in_existing_vpc(
        self, name, config, vpc_id, subnet_deps, child_opts
    ) -> None:
        """Public subnets for the internet-facing load balancer, in their VPC.

        An ALB is only accepted in a subnet whose route table reaches an internet
        gateway, and the gateway in an adopted VPC is the customer's. We add a route
        table of our own that points at it rather than touching theirs.

        A failed lookup is a plain Exception whatever went wrong, so the message is
        the only thing that separates "they have no gateway" from a throttle or a
        bad credential. Anything we cannot read that way is left alone.
        """
        try:
            gateway = aws.ec2.get_internet_gateway(
                filters=[{"name": "attachment.vpc-id", "values": [vpc_id]}]
            )
        except Exception as exc:
            if "no matching" not in str(exc).lower():
                raise
            raise ValueError(
                f"No internet gateway found attached to VPC {vpc_id}, so an "
                "internet-facing load balancer cannot be placed in it. Attach one, or "
                "deploy with public access disabled to reach the data plane over "
                "PrivateLink."
            ) from exc

        self.public_route_table = aws.ec2.RouteTable(
            f"{name}-existing-public-rt",
            vpc_id=vpc_id,
            tags=config.tags(Name=f"{config.resource_prefix}-existing-public-rt"),
            opts=child_opts,
        )
        self.public_route = aws.ec2.Route(
            f"{name}-existing-public-route",
            route_table_id=self.public_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=gateway.id,
            opts=child_opts,
        )

        for i, az in enumerate(config.availability_zones):
            subnet = vpc_subnet.create(
                name,
                config,
                vpc_id,
                az,
                i,
                is_public=True,
                opts=pulumi.ResourceOptions(parent=self, depends_on=subnet_deps),
            )
            self.public_subnets.append(subnet)

            aws.ec2.RouteTableAssociation(
                f"{name}-existing-public-rta-{az}",
                subnet_id=subnet.id,
                route_table_id=self.public_route_table.id,
                opts=child_opts,
            )

    def _create_lb_backend_sg(
        self,
        name: str,
        config: AWSConfig,
        vpc_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
    ) -> None:
        self.lb_backend_security_group = aws.ec2.SecurityGroup(
            f"{name}-lb-backend-sg",
            vpc_id=vpc_id,
            description="Shared backend security group for load balancers",
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags=config.tags(Name=f"{config.resource_prefix}-lb-backend-sg"),
            opts=opts,
        )

    def _register_outputs(self, vpc_id: pulumi.Input[str]) -> None:
        self._vpc_id = vpc_id
        self._public_subnet_ids = [s.id for s in self.public_subnets]
        self._private_subnet_ids = [s.id for s in self.private_subnets]

        self.register_outputs(
            {
                "vpc_id": self._vpc_id,
                "public_subnet_ids": self._public_subnet_ids,
                "private_subnet_ids": self._private_subnet_ids,
                "lb_backend_security_group_id": self.lb_backend_security_group.id,
            }
        )

    def _create_route_tables(self, name: str, opts: pulumi.ResourceOptions):
        public_rt = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-public-rt"),
            opts=opts,
        )

        aws.ec2.Route(
            f"{name}-public-route",
            route_table_id=public_rt.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=self.igw.id,
            opts=opts,
        )

        for i, subnet in enumerate(self.public_subnets):
            aws.ec2.RouteTableAssociation(
                f"{name}-public-rta-{i}",
                subnet_id=subnet.id,
                route_table_id=public_rt.id,
                opts=opts,
            )

        private_route_table_ids = []

        for i, (subnet, nat) in enumerate(
            zip(self.private_subnets, self.nat_gateways, strict=True)
        ):
            az = self.config.availability_zones[i]
            private_rt = aws.ec2.RouteTable(
                f"{name}-private-rt-{az}",
                vpc_id=self.vpc.id,
                tags=self.config.tags(Name=f"{self.config.resource_prefix}-private-rt-{az}"),
                opts=opts,
            )
            private_route_table_ids.append(private_rt.id)

            aws.ec2.Route(
                f"{name}-private-route-{az}",
                route_table_id=private_rt.id,
                destination_cidr_block="0.0.0.0/0",
                nat_gateway_id=nat.id,
                opts=opts,
            )

            aws.ec2.RouteTableAssociation(
                f"{name}-private-rta-{az}",
                subnet_id=subnet.id,
                route_table_id=private_rt.id,
                opts=opts,
            )

        # S3 gateway endpoint routes traffic directly over the AWS network,
        aws.ec2.VpcEndpoint(
            f"{name}-s3-endpoint",
            vpc_id=self.vpc.id,
            service_name=f"com.amazonaws.{self.config.region}.s3",
            vpc_endpoint_type="Gateway",
            route_table_ids=private_route_table_ids,
            tags=self.config.tags(Name=f"{self.config.resource_prefix}-s3-endpoint"),
            opts=opts,
        )

    @property
    def vpc_id(self) -> pulumi.Input[str]:
        return self._vpc_id

    @property
    def lb_backend_security_group_id(self) -> pulumi.Output[str]:
        return self.lb_backend_security_group.id

    @property
    def public_subnet_ids(self) -> list[pulumi.Input[str]]:
        return self._public_subnet_ids

    @property
    def private_subnet_ids(self) -> list[pulumi.Input[str]]:
        return self._private_subnet_ids

    @property
    def private_subnet_cidrs(self) -> list[str]:
        return [
            str(vpc_subnet.cidr(self.config.vpc_cidr, i, is_public=False))
            for i in range(len(self.config.availability_zones))
        ]
