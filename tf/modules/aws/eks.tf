data "aws_caller_identity" "current" {}

resource "aws_iam_role" "eks_cluster" {
  name_prefix = "${local.resource_prefix}-eks-cluster-role-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-cluster-role" })
}

resource "aws_iam_role" "eks_service" {
  name_prefix = "${local.resource_prefix}-eks-cluster-eksRole-role-"
  description = "Allows EKS to manage clusters on your behalf."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["eks.amazonaws.com"] }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController",
  ])
  role       = aws_iam_role.eks_cluster.name
  policy_arn = each.value
}

resource "aws_iam_role_policy_attachment" "eks_service" {
  role       = aws_iam_role.eks_service.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role" "node" {
  name_prefix = "${local.resource_prefix}-eks-node-role-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-node-role" })
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    "arn:aws:iam::aws:policy/AmazonRoute53FullAccess",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_security_group" "eks_cluster" {
  name_prefix            = "${local.resource_prefix}-eks-cluster-eksClusterSecurityGroup-"
  vpc_id                 = aws_vpc.this.id
  description            = "Managed by Pulumi"
  revoke_rules_on_delete = true
  tags                   = merge(local.tags, { Name = "${local.resource_prefix}-eks-cluster-eksClusterSecurityGroup" })
}

resource "aws_security_group_rule" "eks_cluster_internet_egress" {
  type              = "egress"
  security_group_id = aws_security_group.eks_cluster.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow internet access."
}

resource "aws_security_group" "eks_node" {
  name_prefix            = "${local.resource_prefix}-eks-cluster-nodeSecurityGroup-"
  vpc_id                 = aws_vpc.this.id
  description            = "Managed by Pulumi"
  revoke_rules_on_delete = true
  tags = merge(local.tags, {
    Name                                          = "${local.resource_prefix}-eks-cluster-nodeSecurityGroup"
    "kubernetes.io/cluster/${local.cluster_name}" = "owned"
  })

  depends_on = [aws_eks_cluster.this]
}

resource "aws_security_group_rule" "eks_cluster_ingress" {
  type                     = "ingress"
  security_group_id        = aws_security_group.eks_cluster.id
  source_security_group_id = aws_security_group.eks_node.id
  protocol                 = "tcp"
  from_port                = 443
  to_port                  = 443
  description              = "Allow pods to communicate with the cluster API Server"
}

resource "aws_security_group_rule" "eks_node_cluster_ingress" {
  type                     = "ingress"
  security_group_id        = aws_security_group.eks_node.id
  source_security_group_id = aws_security_group.eks_cluster.id
  protocol                 = "tcp"
  from_port                = 1025
  to_port                  = 65535
  description              = "Allow worker Kubelets and pods to receive communication from the cluster control plane"
}

resource "aws_security_group_rule" "eks_node_ingress" {
  type              = "ingress"
  security_group_id = aws_security_group.eks_node.id
  self              = true
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  description       = "Allow nodes to communicate with each other"
}

resource "aws_security_group_rule" "eks_ext_api_server_cluster_ingress" {
  type                     = "ingress"
  security_group_id        = aws_security_group.eks_node.id
  source_security_group_id = aws_security_group.eks_cluster.id
  protocol                 = "tcp"
  from_port                = 443
  to_port                  = 443
  description              = "Allow pods running extension API servers on port 443 to receive communication from cluster control plane"
}

resource "aws_security_group_rule" "eks_node_internet_egress" {
  type              = "egress"
  security_group_id = aws_security_group.eks_node.id
  protocol          = "-1"
  from_port         = 0
  to_port           = 0
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "Allow internet access."
}

resource "aws_cloudwatch_log_group" "eks_cluster" {
  name = "/aws/eks/${local.cluster_name}/cluster"
  tags = merge(local.tags, { Name = "${local.resource_prefix}-eks-cluster-logs" })
}

resource "aws_eks_cluster" "this" {
  name     = local.cluster_name
  role_arn = aws_iam_role.eks_service.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = concat([for s in aws_subnet.public : s.id], [for s in aws_subnet.private : s.id])
    security_group_ids      = [aws_security_group.eks_cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  bootstrap_self_managed_addons = true
  enabled_cluster_log_types     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  tags                          = merge(local.tags, { Name = "${local.resource_prefix}-eks-cluster-eksCluster" })
  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster,
    aws_iam_role_policy_attachment.eks_service,
    aws_cloudwatch_log_group.eks_cluster,
    aws_security_group_rule.eks_cluster_internet_egress,
  ]
}

data "tls_certificate" "eks" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

data "aws_eks_addon_version" "kube_proxy" {
  addon_name         = "kube-proxy"
  kubernetes_version = aws_eks_cluster.this.version
  most_recent        = true
}

data "aws_eks_addon_version" "vpc_cni" {
  addon_name         = "vpc-cni"
  kubernetes_version = aws_eks_cluster.this.version
  most_recent        = true
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "kube-proxy"
  addon_version               = data.aws_eks_addon_version.kube_proxy.version
  preserve                    = true
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = local.tags
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  addon_version               = data.aws_eks_addon_version.vpc_cni.version
  preserve                    = true
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  configuration_values = jsonencode({
    env = {
      AWS_VPC_ENI_MTU                    = "9001"
      AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG = "false"
      AWS_VPC_K8S_CNI_EXTERNALSNAT       = "false"
      AWS_VPC_K8S_CNI_LOGLEVEL           = "DEBUG"
      AWS_VPC_K8S_CNI_LOG_FILE           = "/host/var/log/aws-routed-eni/ipamd.log"
      AWS_VPC_K8S_CNI_VETHPREFIX         = "eni"
      AWS_VPC_K8S_PLUGIN_LOG_FILE        = "/var/log/aws-routed-eni/plugin.log"
      AWS_VPC_K8S_PLUGIN_LOG_LEVEL       = "DEBUG"
      ENABLE_POD_ENI                     = "false"
      WARM_ENI_TARGET                    = "1"
    }
    init = {
      env = {
        DISABLE_TCP_EARLY_DEMUX = "false"
      }
    }
  })
  tags = local.tags
}

locals {
  oidc_url_noscheme = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")
  node_user_data = var.custom_ami_id == null ? null : base64encode(<<-EOT
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==BOUNDARY=="

--==BOUNDARY==
Content-Type: application/node.eks.aws

---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    name: ${aws_eks_cluster.this.name}
    apiServerEndpoint: ${aws_eks_cluster.this.endpoint}
    certificateAuthority: ${aws_eks_cluster.this.certificate_authority[0].data}
    cidr: ${aws_eks_cluster.this.kubernetes_network_config[0].service_ipv4_cidr}
--==BOUNDARY==--
EOT
  )
}

resource "aws_launch_template" "node" {
  for_each               = { for np in var.node_pools : np.name => np }
  name                   = "${local.resource_prefix}-${each.key}-lt-${local.resource_suffix}"
  image_id               = var.custom_ami_id
  user_data              = local.node_user_data
  update_default_version = true

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = each.value.disk_size_gb
      delete_on_termination = true
    }
  }

  metadata_options {
    http_put_response_hop_limit = 2
    http_tokens                 = "optional"
  }

  tag_specifications {
    resource_type = "instance"
    tags          = local.tags
  }
  tag_specifications {
    resource_type = "volume"
    tags          = local.tags
  }
  tags = merge(local.tags, { Name = "${local.resource_prefix}-${each.key}-lt" })
}

resource "aws_eks_node_group" "this" {
  for_each        = { for np in var.node_pools : np.name => np }
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${local.resource_prefix}-${each.key}-${local.resource_suffix}"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [for s in aws_subnet.private : s.id]
  ami_type        = var.custom_ami_id == null ? "AL2023_x86_64_STANDARD" : "CUSTOM"
  instance_types  = [each.value.instance_type]

  scaling_config {
    desired_size = each.value.desired_size
    min_size     = each.value.min_size
    max_size     = each.value.max_size
  }

  launch_template {
    id      = aws_launch_template.node[each.key].id
    version = aws_launch_template.node[each.key].latest_version
  }

  labels = merge(each.value.labels, {
    "pinecone.io/nodepool" = each.key
    "pinecone.io/cell"     = local.cell_name
  })

  dynamic "taint" {
    for_each = each.value.taints
    content {
      key    = taint.value.key
      value  = taint.value.value
      effect = taint.value.effect
    }
  }

  tags = merge(local.tags, { Name = "${local.resource_prefix}-${each.key}" })
  depends_on = [
    aws_iam_role_policy_attachment.node,
    aws_eks_addon.kube_proxy,
    aws_eks_addon.vpc_cni,
    aws_security_group_rule.eks_cluster_ingress,
    aws_security_group_rule.eks_ext_api_server_cluster_ingress,
    aws_security_group_rule.eks_node_cluster_ingress,
    aws_security_group_rule.eks_node_ingress,
    aws_security_group_rule.eks_node_internet_egress,
  ]

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }
}

data "aws_eks_cluster_auth" "this" {
  name = aws_eks_cluster.this.name
}
