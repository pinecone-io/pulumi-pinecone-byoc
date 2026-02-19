"""Internal load balancer with Private Link Service."""

import pulumi
import pulumi_kubernetes as k8s
from pulumi_azure_native import network

from config.azure import AzureConfig

from ..common.naming import DNS_CNAMES


class InternalLoadBalancer(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: AzureConfig,
        k8s_provider: k8s.Provider,
        resource_group_name: pulumi.Input[str],
        pls_subnet_name: pulumi.Output[str],
        dns_zone_name: pulumi.Output[str],
        subdomain: pulumi.Output[str],
        external_ip_address: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        public_access_enabled: bool = True,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:InternalLoadBalancer", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        tls_secret_name = subdomain.apply(lambda s: f"{s.split('.')[0]}-tls")

        # placeholder TLS secret: cert-manager overwrites with real cert later,
        # ignore_changes prevents Pulumi from reverting
        placeholder_tls_secret = k8s.core.v1.Secret(
            f"{name}-placeholder-tls",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=tls_secret_name,
                namespace="gloo-system",
            ),
            type="kubernetes.io/tls",
            string_data={"tls.crt": "", "tls.key": ""},
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                ignore_changes=[
                    "data",
                    "stringData",
                    "metadata.annotations",
                    "metadata.labels",
                ],
            ),
        )

        # internal LB service with Private Link Service (PLS) annotations
        # AKS auto-creates the PLS when azure-pls-create is set
        self._internal_lb_service = k8s.core.v1.Service(
            f"{name}-internal-lb",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="gateway-proxy-internal-lb",
                namespace="gloo-system",
                labels={
                    "app.kubernetes.io/managed-by": "pulumi",
                    "app.kubernetes.io/component": "internal-load-balancer",
                },
                annotations={
                    "service.beta.kubernetes.io/azure-load-balancer-internal": "true",
                    "service.beta.kubernetes.io/azure-pls-create": "true",
                    "service.beta.kubernetes.io/azure-pls-name": self._cell_name.apply(
                        lambda cn: f"{cn}-pls"
                    ),
                    "service.beta.kubernetes.io/azure-pls-visibility": "*",
                    "service.beta.kubernetes.io/azure-pls-auto-approval": config.subscription_id,
                    "service.beta.kubernetes.io/azure-pls-ip-configuration-subnet": pls_subnet_name,
                    "service.beta.kubernetes.io/azure-pls-proxy-protocol": "false",
                },
            ),
            spec=k8s.core.v1.ServiceSpecArgs(
                type="LoadBalancer",
                selector={
                    "gloo": "gateway-proxy",
                },
                ports=[
                    k8s.core.v1.ServicePortArgs(
                        name="https",
                        port=443,
                        target_port=8443,
                        protocol="TCP",
                    ),
                ],
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                depends_on=[placeholder_tls_secret],
                custom_timeouts=pulumi.CustomTimeouts(create="20m", update="20m"),
            ),
        )

        # public LB service for external access (uses external IP from DNS component)
        if public_access_enabled:
            k8s.core.v1.Service(
                f"{name}-public-lb",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="gateway-proxy-public-lb",
                    namespace="gloo-system",
                    labels={
                        "app.kubernetes.io/managed-by": "pulumi",
                        "app.kubernetes.io/component": "public-load-balancer",
                    },
                    annotations={
                        "service.beta.kubernetes.io/azure-load-balancer-resource-group": resource_group_name,
                        "service.beta.kubernetes.io/azure-load-balancer-ipv4": external_ip_address,
                    },
                ),
                spec=k8s.core.v1.ServiceSpecArgs(
                    type="LoadBalancer",
                    selector={
                        "gloo": "gateway-proxy",
                    },
                    ports=[
                        k8s.core.v1.ServicePortArgs(
                            name="https",
                            port=443,
                            target_port=8443,
                            protocol="TCP",
                        ),
                    ],
                ),
                opts=pulumi.ResourceOptions(
                    parent=self,
                    provider=k8s_provider,
                    custom_timeouts=pulumi.CustomTimeouts(create="20m", update="20m"),
                ),
            )

        # ingress for cert-manager: triggers ingress-shim to auto-create Certificate CR.
        # no ingress controller needed — cert-manager watches Ingress annotations directly.
        self._ingress = k8s.networking.v1.Ingress(
            f"{name}-ingress",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="private-gloo-lb",
                namespace="gloo-system",
                labels={
                    "app.kubernetes.io/managed-by": "pulumi",
                    "app.kubernetes.io/component": "private-load-balancer",
                },
                annotations={
                    "cert-manager.io/issuer": "letsencrypt-prod",
                    "kubernetes.io/ingress.allow-http": "false",
                    "pulumi.com/patchForce": "true",
                    "pulumi.com/skipAwait": "true",
                },
            ),
            spec=k8s.networking.v1.IngressSpecArgs(
                default_backend=k8s.networking.v1.IngressBackendArgs(
                    service=k8s.networking.v1.IngressServiceBackendArgs(
                        name="gateway-proxy",
                        port=k8s.networking.v1.ServiceBackendPortArgs(number=443),
                    ),
                ),
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
                                )
                            ],
                        ),
                    )
                ],
                tls=[
                    k8s.networking.v1.IngressTLSArgs(
                        hosts=[
                            subdomain.apply(lambda s: f"*.{s}"),
                            subdomain.apply(lambda s: f"*.svc.{s}"),
                            subdomain.apply(lambda s: f"*.private.{s}"),
                            subdomain.apply(lambda s: f"*.svc.private.{s}"),
                        ],
                        secret_name=tls_secret_name,
                    )
                ],
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                delete_before_replace=True,
                depends_on=[placeholder_tls_secret],
                custom_timeouts=pulumi.CustomTimeouts(create="20m", update="20m"),
            ),
        )

        # extract internal LB IP from service status
        lb_ip = self._internal_lb_service.status.apply(
            lambda s: (
                s.load_balancer.ingress[0].ip
                if s and s.load_balancer and s.load_balancer.ingress
                else None
            )
        )

        # private DNS A record: private.{zone} -> internal LB IP
        private_a_record = network.RecordSet(
            f"{name}-private-a-record",
            resource_group_name=resource_group_name,
            zone_name=dns_zone_name,
            relative_record_set_name="private",
            record_type="A",
            ttl=300,
            a_records=[
                network.ARecordArgs(ipv4_address=lb_ip),
            ],
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._internal_lb_service],
            ),
        )

        # private CNAME records: {cname}.private -> private.{zone}
        private_cname_records = []
        for cname in DNS_CNAMES:
            private_cname = network.RecordSet(
                f"{name}-{cname.replace('*.', 'wildcard-').replace('.', '-')}-private-cname",
                resource_group_name=resource_group_name,
                zone_name=dns_zone_name,
                relative_record_set_name=f"{cname}.private",
                record_type="CNAME",
                ttl=300,
                cname_record=network.CnameRecordArgs(
                    cname=subdomain.apply(lambda s: f"private.{s}"),
                ),
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[self._internal_lb_service],
                ),
            )
            private_cname_records.append(private_cname)

        self._lb_ip = lb_ip
        self._pls_name = self._cell_name.apply(lambda cn: f"{cn}-pls")
        self._private_a_record = private_a_record
        self._private_cname_records = private_cname_records

        self.register_outputs(
            {
                "lb_ip": lb_ip,
                "pls_name": self._pls_name,
            }
        )

    @property
    def internal_lb_service(self) -> k8s.core.v1.Service:
        return self._internal_lb_service

    @property
    def lb_ip(self) -> pulumi.Output[str]:
        return self._lb_ip

    @property
    def pls_name(self) -> pulumi.Output[str]:
        return self._pls_name
