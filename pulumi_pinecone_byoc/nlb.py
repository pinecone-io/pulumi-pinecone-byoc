"""
NLB component for Pinecone BYOC infrastructure.

Creates a Network Load Balancer for private endpoint access.
Architecture: NLB -> Private ALB -> gateway-proxy pods
"""

import time
from typing import Optional

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from config import Config
from .vpc import VPC
from .dns import DNS


class NLB(pulumi.ComponentResource):
    """
    Creates a Network Load Balancer with:
    - Private ALB created via Kubernetes Ingress
    - NLB targeting the private ALB
    - TLS termination at NLB using ACM certificate
    """

    def __init__(
        self,
        name: str,
        config: Config,
        vpc: VPC,
        dns: DNS,
        k8s_provider: pulumi.ProviderResource,
        cluster_security_group_id: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:NLB", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)

        self.nlb_security_group = aws.ec2.SecurityGroup(
            f"{name}-nlb-sg",
            vpc_id=vpc.vpc_id,
            description=f"Security group for {config.resource_prefix} NLB",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=443,
                    to_port=443,
                    cidr_blocks=["0.0.0.0/0"],
                    description="HTTPS from anywhere",
                ),
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="tcp",
                    from_port=443,
                    to_port=443,
                    cidr_blocks=["0.0.0.0/0"],
                    description="HTTPS to ALB",
                ),
            ],
            tags=config.tags(Name=f"{config.resource_prefix}-nlb-sg"),
            opts=child_opts,
        )

        self.alb_security_group = aws.ec2.SecurityGroup(
            f"{name}-private-alb-sg",
            vpc_id=vpc.vpc_id,
            description=f"Security group for {config.resource_prefix} private ALB - only from NLB",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=443,
                    to_port=443,
                    security_groups=[self.nlb_security_group.id],
                    description="HTTPS from NLB only",
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
            tags=config.tags(Name=f"{config.resource_prefix}-private-alb-sg"),
            opts=child_opts,
        )

        # build annotations - let AWS LB Controller manage security groups automatically
        # (allows controller to create rules for ALB -> pod traffic)
        def build_http2_annotations(cert_arn: str, subdomain: str, cn: str) -> dict:
            return {
                "kubernetes.io/ingress.class": "alb",
                "alb.ingress.kubernetes.io/group.name": "private-pinecone",
                "alb.ingress.kubernetes.io/load-balancer-name": f"{cn}-private-alb",
                "alb.ingress.kubernetes.io/scheme": "internal",
                "alb.ingress.kubernetes.io/target-type": "ip",
                "alb.ingress.kubernetes.io/healthcheck-path": "/",
                "alb.ingress.kubernetes.io/healthcheck-protocol": "HTTPS",
                "alb.ingress.kubernetes.io/backend-protocol-version": "HTTP2",
                "alb.ingress.kubernetes.io/backend-protocol": "HTTPS",
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS": 443}]',
                "alb.ingress.kubernetes.io/conditions.gateway-proxy": '[{"field":"http-header","httpHeaderConfig":{"httpHeaderName": "Content-Type", "values":["application/grpc"]}}]',
                "alb.ingress.kubernetes.io/certificate-arn": cert_arn,
                "external-dns.alpha.kubernetes.io/hostname": f"private-ingress.{subdomain}.pinecone.io",
                "external-dns.alpha.kubernetes.io/ingress-hostname-source": "annotation-only",
                "alb.ingress.kubernetes.io/group.order": "1",
            }

        http2_annotations = pulumi.Output.all(
            dns.private_certificate_arn, dns.subdomain, self._cell_name
        ).apply(lambda args: build_http2_annotations(*args))

        # private ingress for HTTP2/gRPC traffic
        private_lb_http2 = k8s.networking.v1.Ingress(
            f"{name}-private-gloo-lb",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="private-gloo-lb",
                namespace="gloo-system",
                annotations=http2_annotations,
            ),
            spec=k8s.networking.v1.IngressSpecArgs(
                rules=[
                    k8s.networking.v1.IngressRuleArgs(
                        host="*.pinecone.io",
                        http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                            paths=[
                                k8s.networking.v1.HTTPIngressPathArgs(
                                    path="/",
                                    path_type="Prefix",
                                    backend=k8s.networking.v1.IngressBackendArgs(
                                        service=k8s.networking.v1.IngressServiceBackendArgs(
                                            name="gateway-proxy",
                                            port=k8s.networking.v1.ServiceBackendPortArgs(
                                                number=443,
                                            ),
                                        ),
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
                tls=[
                    k8s.networking.v1.IngressTLSArgs(
                        hosts=dns.private_dns_domains,
                    ),
                ],
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                delete_before_replace=True,
            ),
        )

        def build_http1_annotations(cert_arn: str, cn: str) -> dict:
            return {
                "kubernetes.io/ingress.class": "alb",
                "alb.ingress.kubernetes.io/group.name": "private-pinecone",
                "alb.ingress.kubernetes.io/load-balancer-name": f"{cn}-private-alb",
                "alb.ingress.kubernetes.io/scheme": "internal",
                "alb.ingress.kubernetes.io/target-type": "ip",
                "alb.ingress.kubernetes.io/healthcheck-path": "/",
                "alb.ingress.kubernetes.io/healthcheck-protocol": "HTTPS",
                "alb.ingress.kubernetes.io/backend-protocol-version": "HTTP1",
                "alb.ingress.kubernetes.io/backend-protocol": "HTTPS",
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS": 443}]',
                "alb.ingress.kubernetes.io/certificate-arn": cert_arn,
                "alb.ingress.kubernetes.io/group.order": "2",
                "external-dns.alpha.kubernetes.io/ingress-hostname-source": "annotation-only",
            }

        http1_annotations = pulumi.Output.all(
            dns.private_certificate_arn, self._cell_name
        ).apply(lambda args: build_http1_annotations(*args))

        # private ingress for HTTP1 traffic
        private_lb_http1 = k8s.networking.v1.Ingress(
            f"{name}-private-gloo-lb-http1",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="private-gloo-lb-http1",
                namespace="gloo-system",
                annotations=http1_annotations,
            ),
            spec=k8s.networking.v1.IngressSpecArgs(
                rules=[
                    k8s.networking.v1.IngressRuleArgs(
                        http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                            paths=[
                                k8s.networking.v1.HTTPIngressPathArgs(
                                    path="/",
                                    path_type="Exact",
                                    backend=k8s.networking.v1.IngressBackendArgs(
                                        service=k8s.networking.v1.IngressServiceBackendArgs(
                                            name="gateway-proxy",
                                            port=k8s.networking.v1.ServiceBackendPortArgs(
                                                number=443,
                                            ),
                                        ),
                                    ),
                                ),
                            ],
                        ),
                    ),
                    k8s.networking.v1.IngressRuleArgs(
                        host="*.pinecone.io",
                        http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                            paths=[
                                k8s.networking.v1.HTTPIngressPathArgs(
                                    path="/",
                                    path_type="Prefix",
                                    backend=k8s.networking.v1.IngressBackendArgs(
                                        service=k8s.networking.v1.IngressServiceBackendArgs(
                                            name="gateway-proxy",
                                            port=k8s.networking.v1.ServiceBackendPortArgs(
                                                number=443,
                                            ),
                                        ),
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
                tls=[
                    k8s.networking.v1.IngressTLSArgs(
                        hosts=dns.private_dns_domains,
                    ),
                ],
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                delete_before_replace=True,
                depends_on=[private_lb_http2],
            ),
        )

        # target group for the private ALB
        self.target_group = aws.lb.TargetGroup(
            f"{name}-alb-tg",
            name=self._cell_name.apply(lambda cn: f"{cn}-tg"[:32]),  # AWS tg name limit
            target_type="alb",
            port=443,
            protocol="TCP",
            vpc_id=vpc.vpc_id,
            health_check=aws.lb.TargetGroupHealthCheckArgs(
                enabled=True,
                port="traffic-port",
                protocol="HTTPS",
            ),
            tags=self._cell_name.apply(lambda cn: config.tags(Name=f"{cn}-alb-tg")),
            opts=child_opts,
        )

        # wait for private ALB to be created and attach it to target group
        # the ALB is created by the AWS Load Balancer Controller when ingress is applied
        def get_private_alb_arn(ingress_status, cn: str):
            """Wait for private ALB and return its ARN."""
            import boto3

            elbv2 = boto3.client("elbv2")
            alb_name = f"{cn}-private-alb"
            for _ in range(30):
                try:
                    resp = elbv2.describe_load_balancers(Names=[alb_name])
                    albs = resp.get("LoadBalancers", [])
                    if albs:
                        return albs[0]["LoadBalancerArn"]
                except Exception:
                    pass
                pulumi.log.info(f"Waiting for private ALB {alb_name}...")
                time.sleep(10)
            raise Exception(f"Failed to find private ALB {alb_name}")

        private_alb_arn = pulumi.Output.all(
            private_lb_http2.status, private_lb_http1.status, self._cell_name
        ).apply(lambda args: get_private_alb_arn(args[0], args[2]))

        aws.lb.TargetGroupAttachment(
            f"{name}-tg-attachment",
            target_group_arn=self.target_group.arn,
            target_id=private_alb_arn,
            port=443,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[private_lb_http2, private_lb_http1],
            ),
        )

        self.nlb = aws.lb.LoadBalancer(
            f"{name}-nlb",
            name=f"{config.resource_prefix}-nlb",
            internal=True,
            load_balancer_type="network",
            subnets=vpc.private_subnet_ids,
            security_groups=[self.nlb_security_group.id],
            enable_cross_zone_load_balancing=True,
            tags=config.tags(Name=f"{config.resource_prefix}-nlb"),
            opts=child_opts,
        )

        # TCP Listener (TLS termination happens at private ALB)
        self.listener = aws.lb.Listener(
            f"{name}-listener",
            load_balancer_arn=self.nlb.arn,
            port=443,
            protocol="TCP",
            default_actions=[
                aws.lb.ListenerDefaultActionArgs(
                    type="forward",
                    target_group_arn=self.target_group.arn,
                ),
            ],
            tags=config.tags(Name=f"{config.resource_prefix}-listener"),
            opts=child_opts,
        )

        # PrivateLink setup for private endpoint access
        # consumers create VPC endpoints to this service and get DNS resolution via PrivateLink
        subdomain = dns.subdomain
        self.vpc_endpoint_service = aws.ec2.VpcEndpointService(
            f"{name}-vpces",
            acceptance_required=False,
            allowed_principals=["*"],
            network_load_balancer_arns=[self.nlb.arn],
            private_dns_name=pulumi.Output.from_input(subdomain).apply(
                lambda s: f"*.private.{s}.byoc.pinecone.io"
            ),
            opts=child_opts,
        )

        record_name = self.vpc_endpoint_service.private_dns_name_configurations.apply(
            lambda configs: str(configs[0].name) if configs else ""
        )
        record_value = self.vpc_endpoint_service.private_dns_name_configurations.apply(
            lambda configs: str(configs[0].value) if configs else ""
        )

        txt_record = aws.route53.Record(
            f"{name}-privatelink-dns-verification",
            zone_id=dns.zone_id,
            name=pulumi.Output.all(record_name, dns.fqdn).apply(
                lambda args: f"{args[0]}.{args[1]}" if args[0] else ""
            ),
            records=[record_value],
            type="TXT",
            ttl=1800,
            opts=child_opts,
        )

        # wait for domain verification before creating VPC endpoint with private DNS
        # AWS verifies the TXT record asynchronously, so we poll until verified
        def wait_for_domain_verification(args) -> str:
            service_id, service_name, _txt_fqdn = (
                args  # _txt_fqdn ensures TXT record is created first
            )
            import boto3

            ec2 = boto3.client("ec2")

            resp = ec2.describe_vpc_endpoint_service_configurations(
                ServiceIds=[service_id]
            )
            configs = resp.get("ServiceConfigurations", [])
            if configs:
                state = (
                    configs[0].get("PrivateDnsNameConfiguration", {}).get("State", "")
                )
                if state == "verified":
                    pulumi.log.info(
                        f"Private DNS domain already verified for {service_id}"
                    )
                    return service_name

            pulumi.log.info("Waiting 60s for DNS propagation before verification...")
            time.sleep(60)

            max_attempts = 30
            for attempt in range(max_attempts):
                # trigger verification on first attempt and every 3rd attempt
                if attempt % 3 == 0:
                    try:
                        ec2.start_vpc_endpoint_service_private_dns_verification(
                            ServiceId=service_id
                        )
                    except Exception:
                        pass
                resp = ec2.describe_vpc_endpoint_service_configurations(
                    ServiceIds=[service_id]
                )
                configs = resp.get("ServiceConfigurations", [])
                if configs:
                    dns_configs = configs[0].get("PrivateDnsNameConfiguration", {})
                    state = dns_configs.get("State", "")
                    if state == "verified":
                        pulumi.log.info(f"Private DNS domain verified for {service_id}")
                        return service_name
                    elif state == "failed":
                        raise Exception(
                            f"Private DNS domain verification failed for {service_id}"
                        )
                    pulumi.log.info(
                        f"Waiting for domain verification ({state})... attempt {attempt + 1}/{max_attempts}"
                    )
                time.sleep(10)
            raise Exception(f"Timeout waiting for domain verification for {service_id}")

        # service_name that only resolves after domain verification completes
        # include txt_record.fqdn to ensure TXT record is created before we start waiting
        verified_service_name = pulumi.Output.all(
            self.vpc_endpoint_service.id,
            self.vpc_endpoint_service.service_name,
            txt_record.fqdn,
        ).apply(wait_for_domain_verification)

        # VPC Endpoint for internal access (integration tests, SLI checkers)
        # this allows pods in the cluster to access *.svc.private.* via PrivateLink
        self.vpc_endpoint = aws.ec2.VpcEndpoint(
            f"{name}-vpce-internal",
            service_name=verified_service_name,
            vpc_endpoint_type="Interface",
            vpc_id=vpc.vpc_id,
            subnet_ids=vpc.private_subnet_ids,
            security_group_ids=[cluster_security_group_id],
            private_dns_enabled=True,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.vpc_endpoint_service],
                custom_timeouts=pulumi.CustomTimeouts(create="15m"),
            ),
        )

        self.register_outputs(
            {
                "nlb_arn": self.nlb.arn,
                "nlb_dns_name": self.nlb.dns_name,
                "target_group_arn": self.target_group.arn,
                "private_alb_security_group_id": self.alb_security_group.id,
                "vpc_endpoint_service_name": self.vpc_endpoint_service.service_name,
            }
        )

    @property
    def nlb_arn(self) -> pulumi.Output[str]:
        return self.nlb.arn

    @property
    def nlb_dns_name(self) -> pulumi.Output[str]:
        return self.nlb.dns_name

    @property
    def target_group_arn(self) -> pulumi.Output[str]:
        return self.target_group.arn
