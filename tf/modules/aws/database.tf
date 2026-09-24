resource "random_password" "db" {
  for_each = local.dbs
  length   = 32
  special  = false
}

resource "aws_db_subnet_group" "this" {
  name       = "${local.resource_prefix}-db-${local.resource_suffix}"
  subnet_ids = [for s in aws_subnet.private : s.id]
  tags       = merge(local.tags, { Name = "${local.resource_prefix}-db-subnet-group" })
}

resource "aws_security_group" "rds" {
  name_prefix = "${local.resource_prefix}-rds-"
  vpc_id      = aws_vpc.this.id
  description = "Security group for ${local.resource_prefix} RDS"
  tags        = merge(local.tags, { Name = "${local.resource_prefix}-rds-sg" })

  ingress {
    protocol    = "tcp"
    from_port   = 5432
    to_port     = 5432
    cidr_blocks = [var.vpc_cidr]
    description = "PostgreSQL from VPC"
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }
}

resource "aws_secretsmanager_secret" "db_master" {
  for_each                = local.dbs
  name                    = "${local.resource_prefix}-${local.resource_suffix}/${each.value.name}/master-password"
  recovery_window_in_days = 0
  tags                    = merge(local.tags, { Name = "${local.resource_prefix}-${each.value.name}-master-password" })
}

resource "aws_secretsmanager_secret_version" "db_master" {
  for_each      = local.dbs
  secret_id     = aws_secretsmanager_secret.db_master[each.key].id
  secret_string = random_password.db[each.key].result
}

resource "aws_rds_cluster_parameter_group" "db" {
  for_each = local.dbs
  family   = "aurora-postgresql15"
  name     = "${local.resource_prefix}-${each.value.name}-params-${local.resource_suffix}"
  parameter {
    name  = "log_statement"
    value = "ddl"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  tags = merge(local.tags, { Name = "${local.resource_prefix}-${each.value.name}-params" })
}

resource "aws_rds_cluster" "db" {
  for_each                        = local.dbs
  cluster_identifier              = "${local.resource_prefix}-${each.value.name}-${local.resource_suffix}"
  engine                          = "aurora-postgresql"
  engine_mode                     = "provisioned"
  engine_version                  = "15.15"
  database_name                   = each.value.db_name
  master_username                 = each.value.username
  master_password                 = random_password.db[each.key].result
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.db[each.key].name
  backup_retention_period         = 7
  preferred_backup_window         = "03:00-04:00"
  preferred_maintenance_window    = "sun:04:00-sun:05:00"
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = !var.deletion_protection
  storage_encrypted               = true
  kms_key_id                      = var.kms_key_arn
  tags                            = merge(local.tags, { Name = "${local.resource_prefix}-${each.value.name}" })
}

resource "aws_rds_cluster_instance" "db" {
  for_each                              = local.dbs
  identifier                            = "${local.resource_prefix}-${each.value.name}-${local.resource_suffix}-0"
  cluster_identifier                    = aws_rds_cluster.db[each.key].id
  instance_class                        = each.value.instance_class
  engine                                = "aurora-postgresql"
  engine_version                        = aws_rds_cluster.db[each.key].engine_version
  publicly_accessible                   = false
  db_subnet_group_name                  = aws_db_subnet_group.this.name
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  auto_minor_version_upgrade            = false
  tags                                  = merge(local.tags, { Name = "${local.resource_prefix}-${each.value.name}-instance-0" })
}

resource "aws_secretsmanager_secret" "db_connection" {
  for_each                = local.dbs
  name                    = "${local.resource_prefix}-${local.resource_suffix}/${each.value.name}/connection"
  recovery_window_in_days = 0
  tags                    = merge(local.tags, { Name = "${local.resource_prefix}-${each.value.name}-connection" })
}

resource "aws_secretsmanager_secret_version" "db_connection" {
  for_each  = local.dbs
  secret_id = aws_secretsmanager_secret.db_connection[each.key].id
  secret_string = jsonencode({
    host     = aws_rds_cluster.db[each.key].endpoint
    port     = aws_rds_cluster.db[each.key].port
    database = aws_rds_cluster.db[each.key].database_name
    username = aws_rds_cluster.db[each.key].master_username
    password = random_password.db[each.key].result
  })
}

