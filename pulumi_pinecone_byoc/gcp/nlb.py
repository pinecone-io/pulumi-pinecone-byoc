"""Internal load balancer with Private Service Connect."""

import time

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s

from config.gcp import GCPConfig

from ..common.naming import DNS_CNAMES


class InternalLoadBalancer(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        config: GCPConfig,
        k8s_provider: k8s.Provider,
        psc_subnet_id: pulumi.Output[str],
        dns_zone_name: pulumi.Output[str],
        subdomain: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        public_access_enabled: bool = True,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:InternalLoadBalancer", name, None, opts)

        self._cell_name = pulumi.Output.from_input(cell_name)

        tls_secret_name = subdomain.apply(lambda s: f"{s.split('.')[0]}-tls")

        # placeholder TLS secret for ingress-gce: it won't configure the LB without this existing.
        # cert-manager overwrites it with the real cert later, ignore_changes prevents Pulumi from reverting
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

        backend_config = k8s.apiextensions.CustomResource(
            f"{name}-backend-config",
            api_version="cloud.google.com/v1",
            kind="BackendConfig",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="edge-nlb-backendconfig",
                namespace="gloo-system",
                labels={
                    "app.kubernetes.io/managed-by": "Helm",
                },
                annotations={
                    "meta.helm.sh/release-name": "netstack",
                    "meta.helm.sh/release-namespace": "gloo-system",
                },
            ),
            spec={
                "timeoutSec": 2147483647,
                "logging": {
                    "enable": True,
                    "sampleRate": 1,
                },
                "healthCheck": {
                    "checkIntervalSec": 5,
                    "timeoutSec": 1,
                    "healthyThreshold": 1,
                    "unhealthyThreshold": 3,
                    "port": 8443,
                    "type": "HTTP2",
                    "requestPath": "/",
                },
                "connectionDraining": {
                    "drainingTimeoutSec": 60,
                },
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=k8s_provider,
                ignore_changes=["metadata.labels", "metadata.annotations"],
            ),
        )

        if public_access_enabled:
            k8s.networking.v1.Ingress(
                f"{name}-public-ingress",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="gloo-lb",
                    namespace="gloo-system",
                    annotations={
                        "cert-manager.io/issuer": "letsencrypt-prod",
                        "kubernetes.io/ingress.allow-http": "false",
                        "networking.gke.io/v1beta1.FrontendConfig": subdomain.apply(
                            lambda s: f"ssl-policy-config-{s.split('.')[0]}"
                        ),
                        "kubernetes.io/ingress.global-static-ip-name": self._cell_name.apply(
                            lambda cn: f"externalip-{cn}"
                        ),
                    },
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
                                                    number=443
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

        ingress = k8s.networking.v1.Ingress(
            f"{name}-ingress",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="private-gloo-lb",
                namespace="gloo-system",
                labels={
                    "app.kubernetes.io/managed-by": "pulumi",
                    "app.kubernetes.io/component": "private-load-balancer",
                },
                annotations={
                    "kubernetes.io/ingress.class": "gce-internal",
                    "kubernetes.io/ingress.allow-http": "false",
                    "cert-manager.io/issuer": "letsencrypt-prod",
                    "pulumi.com/patchForce": "true",
                },
            ),
            spec=k8s.networking.v1.IngressSpecArgs(
                default_backend=k8s.networking.v1.IngressBackendArgs(
                    service=k8s.networking.v1.IngressServiceBackendArgs(
                        name="gateway-proxy",
                        port=k8s.networking.v1.ServiceBackendPortArgs(
                            number=443,
                        ),
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
                depends_on=[backend_config, placeholder_tls_secret],
                custom_timeouts=pulumi.CustomTimeouts(create="20m", update="20m"),
            ),
        )

        def get_lb_ip_and_link(_ingress_status, cell_name_str: str, retries: int = 30):
            for attempt in range(retries):
                try:
                    rules = gcp.compute.get_forwarding_rules(config.project, config.region)
                    lb = next(
                        (
                            r
                            for r in rules.rules
                            if r.subnetwork and r.subnetwork.endswith(cell_name_str)
                        ),
                        None,
                    )
                    if lb is None:
                        pulumi.log.info(
                            f"no matching LB found (attempt {attempt + 1}/{retries}), retrying..."
                        )
                        time.sleep(10)
                        continue
                    return (lb.ip_address, lb.self_link)
                except Exception as e:
                    pulumi.log.info(
                        f"waiting for internal lb (attempt {attempt + 1}/{retries})... {e}"
                    )
                    time.sleep(10)
            raise Exception("failed to get internal LB after retries")

        # wait for Ingress status to be ready, then query the LB
        lb_info = pulumi.Output.all(ingress.status, self._cell_name).apply(
            lambda args: get_lb_ip_and_link(args[0], args[1])
        )

        lb_ip = lb_info.apply(lambda info: info[0])
        lb_link = lb_info.apply(lambda info: info[1])

        service_attachment = gcp.compute.ServiceAttachment(
            f"{name}-service-attachment",
            name=self._cell_name.apply(lambda cn: f"{config.resource_prefix}-psc-{cn}"),
            region=config.region,
            description="Pinecone service attachment",
            connection_preference="ACCEPT_AUTOMATIC",
            enable_proxy_protocol=False,
            nat_subnets=[psc_subnet_id],
            target_service=lb_link,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[ingress],
                delete_before_replace=True,
                retain_on_delete=False,
            ),
        )

        private_ingress_a_record = gcp.dns.RecordSet(
            f"{name}-private-ingress-a-record",
            managed_zone=dns_zone_name,
            name=subdomain.apply(lambda s: f"private.{s}."),
            type="A",
            rrdatas=[lb_ip],
            ttl=300,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[ingress]),
        )

        public_cname_records = []
        if public_access_enabled:
            for cname in DNS_CNAMES:
                public_cname = gcp.dns.RecordSet(
                    f"{name}-{cname.replace('*.', 'wildcard-').replace('.', '-')}-public-cname",
                    managed_zone=dns_zone_name,
                    name=subdomain.apply(lambda s, c=cname: f"{c}.{s}."),
                    type="CNAME",
                    ttl=300,
                    rrdatas=[subdomain.apply(lambda s: f"ingress.{s}.")],
                    opts=pulumi.ResourceOptions(parent=self, depends_on=[ingress]),
                )
                public_cname_records.append(public_cname)

        private_cname_records = []
        for cname in DNS_CNAMES:
            private_cname = gcp.dns.RecordSet(
                f"{name}-{cname.replace('*.', 'wildcard-').replace('.', '-')}-private-cname",
                managed_zone=dns_zone_name,
                name=subdomain.apply(lambda s, c=cname: f"{c}.private.{s}."),
                type="CNAME",
                ttl=300,
                rrdatas=[subdomain.apply(lambda s: f"private.{s}.")],
                opts=pulumi.ResourceOptions(parent=self, depends_on=[ingress]),
            )
            private_cname_records.append(private_cname)

        self._ingress = ingress
        self._service_attachment = service_attachment
        self._lb_ip = lb_ip
        self._private_ingress_a_record = private_ingress_a_record
        self._public_cname_records = public_cname_records
        self._private_cname_records = private_cname_records

        self.register_outputs(
            {
                "lb_ip": lb_ip,
                "service_attachment_name": service_attachment.name,
            }
        )

    @property
    def ingress(self) -> k8s.networking.v1.Ingress:
        return self._ingress

    @property
    def service_attachment(self) -> gcp.compute.ServiceAttachment:
        return self._service_attachment

    @property
    def lb_ip(self) -> pulumi.Output[str]:
        return self._lb_ip
