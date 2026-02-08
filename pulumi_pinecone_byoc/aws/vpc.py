"""
VPC component for Pinecone BYOC infrastructure.

Creates a production-ready VPC with public and private subnets across multiple AZs.
"""

from typing import Optional

import pulumi
import pulumi_aws as aws

from config.aws import AWSConfig


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
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:VPC", name, None, opts)

        self.config = config
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

        self.register_outputs(
            {
                "vpc_id": self.vpc.id,
                "public_subnet_ids": [s.id for s in self.public_subnets],
                "private_subnet_ids": [s.id for s in self.private_subnets],
            }
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

        for i, (subnet, nat) in enumerate(zip(self.private_subnets, self.nat_gateways)):
            az = self.config.availability_zones[i]
            private_rt = aws.ec2.RouteTable(
                f"{name}-private-rt-{az}",
                vpc_id=self.vpc.id,
                tags=self.config.tags(
                    Name=f"{self.config.resource_prefix}-private-rt-{az}"
                ),
                opts=opts,
            )

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

    @property
    def vpc_id(self) -> pulumi.Output[str]:
        return self.vpc.id

    @property
    def public_subnet_ids(self) -> list[pulumi.Output[str]]:
        return [s.id for s in self.public_subnets]

    @property
    def private_subnet_ids(self) -> list[pulumi.Output[str]]:
        return [s.id for s in self.private_subnets]
