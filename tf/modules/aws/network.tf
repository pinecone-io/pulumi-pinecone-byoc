resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = "${local.resource_prefix}-vpc" })

  depends_on = [terraform_data.control_plane_ready]
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.resource_prefix}-igw" })
}

resource "aws_subnet" "public" {
  for_each                = { for i, az in var.availability_zones : az => local.public_subnet_cidrs[i] }
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = each.key
  map_public_ip_on_launch = true
  tags = merge(local.tags, {
    Name                     = "${local.resource_prefix}-public-${each.key}"
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_eip" "nat" {
  for_each = aws_subnet.public
  domain   = "vpc"
  tags     = merge(local.tags, { Name = "${local.resource_prefix}-nat-${each.key}" })
}

resource "aws_nat_gateway" "this" {
  for_each      = aws_subnet.public
  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = each.value.id
  tags          = merge(local.tags, { Name = "${local.resource_prefix}-nat-${each.key}" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_subnet" "private" {
  for_each          = { for i, az in var.availability_zones : az => local.private_subnet_cidrs[i] }
  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = each.key
  tags = merge(local.tags, {
    Name                              = "${local.resource_prefix}-private-${each.key}"
    "kubernetes.io/role/internal-elb" = "1"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.resource_prefix}-public-rt" })
}

resource "aws_route" "public" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  for_each = aws_subnet.private
  vpc_id   = aws_vpc.this.id
  tags     = merge(local.tags, { Name = "${local.resource_prefix}-private-rt-${each.key}" })
}

resource "aws_route" "private" {
  for_each               = aws_route_table.private
  route_table_id         = each.value.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[each.key].id
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for rt in aws_route_table.private : rt.id]
  tags              = merge(local.tags, { Name = "${local.resource_prefix}-s3-endpoint" })
}
