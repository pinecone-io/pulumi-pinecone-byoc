locals {
  alb_controller_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "*"
        Condition = {
          StringEquals = { "iam:AWSServiceName" = "elasticloadbalancing.amazonaws.com" }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "elasticloadbalancing:Describe*",
          "acm:ListCertificates",
          "acm:DescribeCertificate",
          "iam:ListServerCertificates",
          "iam:GetServerCertificate",
          "wafv2:GetWebACL",
          "wafv2:GetWebACLForResource",
          "wafv2:AssociateWebACL",
          "wafv2:DisassociateWebACL",
          "shield:GetSubscriptionState",
          "shield:DescribeProtection",
          "shield:CreateProtection",
          "shield:DeleteProtection"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:DeleteSecurityGroup",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "elasticloadbalancing:CreateLoadBalancer",
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:CreateListener",
          "elasticloadbalancing:DeleteListener",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags",
          "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "elasticloadbalancing:SetIpAddressType",
          "elasticloadbalancing:SetSecurityGroups",
          "elasticloadbalancing:SetSubnets",
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:ModifyTargetGroup",
          "elasticloadbalancing:ModifyTargetGroupAttributes",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:SetWebAcl",
          "elasticloadbalancing:ModifyListener",
          "elasticloadbalancing:AddListenerCertificates",
          "elasticloadbalancing:RemoveListenerCertificates",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:SetRulePriorities",
          "elasticloadbalancing:ModifyListenerAttributes"
        ]
        Resource = "*"
      }
    ]
  }
}

resource "kubernetes_namespace_v1" "gloo_system" {
  metadata {
    name = "gloo-system"
    labels = {
      "kubernetes.io/metadata.name" = "gloo-system"
      name                          = "gloo-system"
    }
  }
  depends_on = [aws_eks_node_group.this]
}

resource "aws_iam_role" "alb_controller" {
  name = "${local.resource_prefix}-alb-controller-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:kube-system:aws-lb-controller-sa"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-alb-controller" })
}

resource "aws_iam_role_policy" "alb_controller" {
  role   = aws_iam_role.alb_controller.name
  policy = jsonencode(local.alb_controller_policy)
}

resource "kubernetes_service_account_v1" "alb_controller" {
  metadata {
    name      = "aws-lb-controller-sa"
    namespace = "kube-system"
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.alb_controller.arn
    }
  }
}

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  namespace  = "kube-system"
  values = [yamlencode({
    region = var.region
    serviceAccount = {
      name   = kubernetes_service_account_v1.alb_controller.metadata[0].name
      create = false
    }
    vpcId             = aws_vpc.this.id
    clusterName       = aws_eks_cluster.this.name
    podLabels         = { app = "aws-lb-controller" }
    enableCertManager = false
  })]
  depends_on = [kubernetes_service_account_v1.alb_controller]
}

resource "aws_iam_role" "cluster_autoscaler" {
  name = "${local.resource_prefix}-cluster-autoscaler-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:kube-system:cluster-autoscaler-sa"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-cluster-autoscaler" })
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  role = aws_iam_role.cluster_autoscaler.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:DescribeAutoScalingInstances",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:DescribeScalingActivities",
          "autoscaling:DescribeTags",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeLaunchTemplateVersions",
          "ec2:DescribeImages",
          "ec2:GetInstanceTypesFromInstanceRequirements",
          "eks:DescribeNodegroup"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["autoscaling:SetDesiredCapacity", "autoscaling:TerminateInstanceInAutoScalingGroup"]
        Resource = "*"
      }
    ]
  })
}

resource "helm_release" "cluster_autoscaler" {
  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = "9.29.3"
  namespace  = "kube-system"
  values = [yamlencode({
    awsRegion     = var.region
    autoDiscovery = { clusterName = aws_eks_cluster.this.name }
    replicaCount  = 2
    rbac = {
      serviceAccount = {
        create = true
        name   = "cluster-autoscaler-sa"
        annotations = {
          "eks.amazonaws.com/role-arn" = aws_iam_role.cluster_autoscaler.arn
        }
      }
    }
    extraArgs = {
      "balance-similar-node-groups"   = "false"
      "skip-nodes-with-local-storage" = "false"
      "scale-down-delay-after-add"    = "30s"
      "scale-down-delay-after-delete" = "0s"
      "scale-down-unneeded-time"      = "30s"
      "max-node-provision-time"       = "10m"
      expander                        = "priority"
    }
    expanderPriorities = {
      "10" = [".*default.*"]
      "1"  = [".*"]
    }
  })]
  depends_on = [aws_iam_role_policy.cluster_autoscaler]
}

resource "aws_iam_role" "external_dns" {
  name = "${local.resource_prefix}-external-dns-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:sub" = [
            "system:serviceaccount:gloo-system:external-dns",
            "system:serviceaccount:gloo-system:certmanager-certgen"
          ]
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-external-dns" })
}

resource "aws_iam_role_policy" "external_dns" {
  role = aws_iam_role.external_dns.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["route53:ChangeResourceRecordSets"]
        Resource = "arn:aws:route53:::hostedzone/*"
      },
      {
        Effect   = "Allow"
        Action   = ["route53:ListHostedZones", "route53:ListHostedZonesByName", "route53:ListResourceRecordSets", "route53:GetChange"]
        Resource = "*"
      }
    ]
  })
}

resource "kubernetes_service_account_v1" "external_dns" {
  metadata {
    name      = "external-dns"
    namespace = kubernetes_namespace_v1.gloo_system.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.external_dns.arn
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name = "${local.resource_prefix}-ebs-csi-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:aud" = "sts.amazonaws.com"
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-ebs-csi" })
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs_csi.arn
  tags                     = local.tags
  depends_on               = [aws_iam_role_policy_attachment.ebs_csi]
}

resource "aws_iam_role" "azrebalance" {
  name = "control-plane-azrebalance-role-${local.cell_name}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:pc-control-plane:suspend-azrebalance-sa"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "control-plane-azrebalance-role-${local.cell_name}" })
}

resource "aws_iam_role_policy" "azrebalance" {
  role = aws_iam_role.azrebalance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:SuspendProcesses"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "amp_ingest" {
  name = "${local.resource_prefix}-amp-ingest-${local.resource_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_url_noscheme}:sub" = "system:serviceaccount:prometheus:amp-iamproxy-ingest-service-account"
        }
      }
    }]
  })
  tags = merge(local.tags, { Name = "${local.resource_prefix}-amp-ingest" })
}

resource "pineconebyoc_amp_access" "this" {
  workload_role_arn = aws_iam_role.amp_ingest.arn
  api_url           = var.api_url
  cpgw_api_key      = pineconebyoc_cpgw_api_key.this.key
  depends_on        = [aws_iam_role.amp_ingest, pineconebyoc_cpgw_api_key.this]
}

resource "aws_iam_role_policy" "amp_assume_pinecone" {
  role = aws_iam_role.amp_ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = pineconebyoc_amp_access.this.pinecone_role_arn
    }]
  })
}

