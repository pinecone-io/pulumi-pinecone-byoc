"""
VPC component for Pinecone BYOC infrastructure.

Creates a production-ready VPC with public and private subnets across multiple AZs.
"""

import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig

RFC1918_RANGES = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
]


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

        if config.existing_vpc_id and (config.private_subnet_ids or config.public_subnet_ids):
            self._adopt(config)
        elif config.existing_vpc_id:
            self._adopt_and_create_subnets(name, config)
        else:
            self._create(name, config)

    def _adopt(self, config: AWSConfig) -> None:
        vpc_id = config.existing_vpc_id
        if not vpc_id:
            raise ValueError("Adopt mode requires existing_vpc_id.")

        vpc = aws.ec2.get_vpc(id=vpc_id)

        private_ids = config.private_subnet_ids or []
        public_ids = config.public_subnet_ids or []
        if not private_ids:
            raise ValueError(
                f"Adopt mode for VPC {vpc_id} requires private_subnet_ids "
                "(one private subnet per availability zone)."
            )

        self._verify_subnets_in_vpc(vpc_id, private_ids)
        if public_ids:
            self._verify_subnets_in_vpc(vpc_id, public_ids)

        self._vpc_id: pulumi.Input[str] = vpc_id
        self._public_subnet_ids: list[pulumi.Input[str]] = list(public_ids)
        self._private_subnet_ids: list[pulumi.Input[str]] = list(private_ids)
        self._vpc_cidr_blocks = [a.cidr_block for a in vpc.cidr_block_associations]

    def _adopt_and_create_subnets(self, name: str, config: AWSConfig) -> None:
        vpc_id = config.existing_vpc_id
        if not vpc_id:
            raise ValueError("Carve mode requires existing_vpc_id.")

        vpc = aws.ec2.get_vpc(id=vpc_id)
        self._validate_cidr(config.vpc_cidr)
        if len(config.availability_zones) > 3:
            raise ValueError(
                f"Maximum 3 AZs supported (got {len(config.availability_zones)}). "
                "Subnet layout does not fit more than 3 AZs in a /16."
            )

        child_opts = pulumi.ResourceOptions(parent=self)

        carve = ipaddress.IPv4Network(config.vpc_cidr)
        covered = any(
            carve.subnet_of(ipaddress.IPv4Network(a.cidr_block))
            for a in vpc.cidr_block_associations
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
                "Give one per availability zone, or none at all to inherit the VPC main "
                "route table."
            )

        self.private_subnets: list[aws.ec2.Subnet] = []
        for i, az in enumerate(config.availability_zones):
            private_subnet = aws.ec2.Subnet(
                f"{name}-private-{az}",
                vpc_id=vpc_id,
                cidr_block=self._calculate_cidr(i, is_public=False),
                availability_zone=az,
                tags=config.tags(
                    Name=f"{config.resource_prefix}-private-{az}",
                    **{"kubernetes.io/role/internal-elb": "1"},
                ),
                opts=pulumi.ResourceOptions(parent=self, depends_on=subnet_deps),
            )
            self.private_subnets.append(private_subnet)

            if route_tables:
                aws.ec2.RouteTableAssociation(
                    f"{name}-private-rta-{az}",
                    subnet_id=private_subnet.id,
                    route_table_id=route_tables[az],
                    opts=child_opts,
                )

        self.public_subnets: list[aws.ec2.Subnet] = []
        if config.public_access:
            self._carve_public_subnets(name, config, vpc_id, subnet_deps, child_opts)

        self._vpc_id = vpc_id
        self._public_subnet_ids = [s.id for s in self.public_subnets]
        self._private_subnet_ids = [s.id for s in self.private_subnets]
        existing_cidrs = [a.cidr_block for a in vpc.cidr_block_associations]
        self._vpc_cidr_blocks = existing_cidrs + ([] if covered else [config.vpc_cidr])

        self.register_outputs(
            {
                "vpc_id": self._vpc_id,
                "public_subnet_ids": self._public_subnet_ids,
                "private_subnet_ids": self._private_subnet_ids,
            }
        )

    @staticmethod
    def _verify_subnets_in_vpc(vpc_id: str, subnet_ids: list[str]) -> None:
        result = aws.ec2.get_subnets(
            filters=[
                {"name": "vpc-id", "values": [vpc_id]},
                {"name": "subnet-id", "values": subnet_ids},
            ]
        )
        missing = [s for s in subnet_ids if s not in set(result.ids)]
        if missing:
            raise ValueError(f"Subnet(s) {', '.join(missing)} were not found in VPC {vpc_id}.")

    def _create(self, name: str, config: AWSConfig) -> None:
        self._validate_cidr(config.vpc_cidr)
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
            # calculate CIDR blocks for each subnet
            # public subnets get smaller blocks, private subnets get larger blocks
            public_cidr = self._calculate_cidr(i, is_public=True)
            private_cidr = self._calculate_cidr(i, is_public=False)

            public_subnet = aws.ec2.Subnet(
                f"{name}-public-{az}",
                vpc_id=self.vpc.id,
                cidr_block=public_cidr,
                availability_zone=az,
                map_public_ip_on_launch=True,
                tags=config.tags(
                    Name=f"{config.resource_prefix}-public-{az}",
                    **{"kubernetes.io/role/elb": "1"},
                ),
                opts=child_opts,
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

            private_subnet = aws.ec2.Subnet(
                f"{name}-private-{az}",
                vpc_id=self.vpc.id,
                cidr_block=private_cidr,
                availability_zone=az,
                tags=config.tags(
                    Name=f"{config.resource_prefix}-private-{az}",
                    **{"kubernetes.io/role/internal-elb": "1"},
                ),
                opts=child_opts,
            )
            self.private_subnets.append(private_subnet)

        self._create_route_tables(name, child_opts)

        self._vpc_id = self.vpc.id
        self._public_subnet_ids = [s.id for s in self.public_subnets]
        self._private_subnet_ids = [s.id for s in self.private_subnets]
        self._vpc_cidr_blocks = [self.vpc.cidr_block]

        self.register_outputs(
            {
                "vpc_id": self._vpc_id,
                "public_subnet_ids": self._public_subnet_ids,
                "private_subnet_ids": self._private_subnet_ids,
            }
        )

    def _carve_public_subnets(self, name, config, vpc_id, subnet_deps, child_opts) -> None:
        """Public subnets for the internet-facing load balancer, in their VPC.

        An ALB is only accepted in a subnet whose route table reaches an internet
        gateway, and the gateway in an adopted VPC is the customer's. We add a route
        table of our own that points at it rather than touching theirs.
        """
        try:
            gateway = aws.ec2.get_internet_gateway(
                filters=[{"name": "attachment.vpc-id", "values": [vpc_id]}]
            )
        except Exception as exc:
            raise ValueError(
                f"No internet gateway found attached to VPC {vpc_id}, so an "
                "internet-facing load balancer cannot be placed in it. Attach one, or "
                "deploy with public access disabled to reach the data plane over "
                "PrivateLink."
            ) from exc

        self.public_route_table = aws.ec2.RouteTable(
            f"{name}-carved-public-rt",
            vpc_id=vpc_id,
            tags=config.tags(Name=f"{config.resource_prefix}-carved-public-rt"),
            opts=child_opts,
        )
        self.public_route = aws.ec2.Route(
            f"{name}-carved-public-route",
            route_table_id=self.public_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=gateway.id,
            opts=child_opts,
        )

        for i, az in enumerate(config.availability_zones):
            subnet = aws.ec2.Subnet(
                f"{name}-public-{az}",
                vpc_id=vpc_id,
                cidr_block=self._calculate_cidr(i, is_public=True),
                availability_zone=az,
                tags=config.tags(
                    Name=f"{config.resource_prefix}-public-{az}",
                    **{"kubernetes.io/role/elb": "1"},
                ),
                opts=pulumi.ResourceOptions(parent=self, depends_on=subnet_deps),
            )
            self.public_subnets.append(subnet)

            aws.ec2.RouteTableAssociation(
                f"{name}-carved-public-rta-{az}",
                subnet_id=subnet.id,
                route_table_id=self.public_route_table.id,
                opts=child_opts,
            )

    @staticmethod
    def _validate_cidr(cidr: str) -> None:
        try:
            network = ipaddress.IPv4Network(cidr)
        except (ValueError, ipaddress.AddressValueError) as e:
            raise ValueError(f"Invalid VPC CIDR '{cidr}': {e}") from e

        if network.prefixlen != 16:
            raise ValueError(
                f"VPC CIDR must be a /16 (got /{network.prefixlen}). "
                "Subnet calculation requires a /16 network."
            )

        if not any(network.subnet_of(rfc1918) for rfc1918 in RFC1918_RANGES):
            raise ValueError(
                f"VPC CIDR '{cidr}' is not in an RFC 1918 private range. "
                "Use a /16 block like 10.0.0.0/16, 172.16.0.0/16, or 192.168.0.0/16. "
                "See https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html"
            )

    def _calculate_cidr(self, index: int, is_public: bool) -> str:
        base = self.config.vpc_cidr.split("/")[0]
        octets = [int(x) for x in base.split(".")]

        if is_public:
            # public subnets: /20 blocks starting at 10.0.0.0, 10.0.16.0, 10.0.32.0
            third_octet = index * 16
            return f"{octets[0]}.{octets[1]}.{third_octet}.0/{self.config.public_subnet_mask}"
        else:
            # private subnets: /18 blocks starting at 10.0.64.0, 10.0.128.0, 10.0.192.0
            third_octet = 64 + (index * 64)
            return f"{octets[0]}.{octets[1]}.{third_octet}.0/{self.config.private_subnet_mask}"

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
    def public_subnet_ids(self) -> list[pulumi.Input[str]]:
        return self._public_subnet_ids

    @property
    def private_subnet_ids(self) -> list[pulumi.Input[str]]:
        return self._private_subnet_ids

    @property
    def private_subnet_cidrs(self) -> list[str]:
        return [
            self._calculate_cidr(i, is_public=False)
            for i in range(len(self.config.availability_zones))
        ]

    @property
    def vpc_cidr_blocks(self) -> list[pulumi.Input[str]]:
        return self._vpc_cidr_blocks
