"""
VPC component for Pinecone BYOC infrastructure.

Creates a production-ready VPC with public and private subnets across multiple AZs.
"""

import ipaddress

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig

MIN_VPC_PREFIX = 16
MAX_VPC_PREFIX = 20

# AWS refuses to load balance in anything narrower than a /27, and wants eight
# addresses free to scale into
PUBLIC_PREFIX_ON_A_SLICE = 26

# the VPC is cut into sixteen slots; a public subnet takes one, a private subnet four
SLOT_BITS = 4
PRIVATE_SLOTS = 4
PRIVATE_FIRST_SLOT = 4

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
            public_cidr = str(self._calculate_cidr(config.vpc_cidr, i, is_public=True))
            private_cidr = str(self._calculate_cidr(config.vpc_cidr, i, is_public=False))

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

        self.register_outputs(
            {
                "vpc_id": self.vpc.id,
                "public_subnet_ids": [s.id for s in self.public_subnets],
                "private_subnet_ids": [s.id for s in self.private_subnets],
            }
        )

    @staticmethod
    def _validate_cidr(cidr: str) -> None:
        try:
            network = ipaddress.IPv4Network(cidr)
        except (ValueError, ipaddress.AddressValueError) as e:
            raise ValueError(f"Invalid VPC CIDR '{cidr}': {e}") from e

        if not MIN_VPC_PREFIX <= network.prefixlen <= MAX_VPC_PREFIX:
            raise ValueError(
                f"VPC CIDR must be between a /{MIN_VPC_PREFIX} and a /{MAX_VPC_PREFIX} "
                f"(got /{network.prefixlen}). The subnet layout needs sixteen slots, and a "
                f"/{MAX_VPC_PREFIX} is the smallest range that still leaves a workable subnet "
                "per availability zone."
            )

        if not any(network.subnet_of(rfc1918) for rfc1918 in RFC1918_RANGES):
            raise ValueError(
                f"VPC CIDR '{cidr}' is not in an RFC 1918 private range. "
                "Use a block inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16. "
                "See https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html"
            )

    @staticmethod
    def _calculate_cidr(vpc_cidr: str, index: int, is_public: bool) -> ipaddress.IPv4Network:
        network = ipaddress.IPv4Network(vpc_cidr)
        slots = list(network.subnets(prefixlen_diff=SLOT_BITS))

        if is_public:
            slot = slots[index]
            prefix = (
                network.prefixlen + SLOT_BITS
                if network.prefixlen == MIN_VPC_PREFIX
                else max(PUBLIC_PREFIX_ON_A_SLICE, slot.prefixlen)
            )
        else:
            slot = slots[PRIVATE_FIRST_SLOT + index * PRIVATE_SLOTS]
            prefix = network.prefixlen + PRIVATE_SLOTS // 2

        return ipaddress.IPv4Network((slot.network_address, prefix))

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
    def vpc_id(self) -> pulumi.Output[str]:
        return self.vpc.id

    @property
    def public_subnet_ids(self) -> list[pulumi.Output[str]]:
        return [s.id for s in self.public_subnets]

    @property
    def private_subnet_ids(self) -> list[pulumi.Output[str]]:
        return [s.id for s in self.private_subnets]

    @property
    def private_subnet_cidrs(self) -> list[str]:
        return [
            str(self._calculate_cidr(self.config.vpc_cidr, i, is_public=False))
            for i in range(len(self.config.availability_zones))
        ]
