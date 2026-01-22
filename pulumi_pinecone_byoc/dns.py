from typing import Optional

import pulumi
import pulumi_aws as aws

from .providers import DnsDelegation, DnsDelegationArgs


class DNS(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        subdomain: pulumi.Input[str],
        parent_zone_name: str,
        api_url: str,
        cpgw_api_key: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:DNS", name, None, opts)

        child_opts = pulumi.ResourceOptions(parent=self)

        tags = {"pinecone:managed-by": "pulumi"}

        def build_fqdn(sub: str) -> str:
            return f"{sub}.{parent_zone_name}"

        fqdn = pulumi.Output.from_input(subdomain).apply(build_fqdn)

        self.zone = aws.route53.Zone(
            f"{name}-zone",
            name=fqdn,
            force_destroy=True,
            tags={**tags, "Name": f"{name}-zone"},
            opts=child_opts,
        )

        self.delegation = DnsDelegation(
            f"{name}-delegation",
            DnsDelegationArgs(
                subdomain=subdomain,
                nameservers=self.zone.name_servers,
                api_url=api_url,
                cpgw_api_key=cpgw_api_key,
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self.zone]),
        )

        # create CNAME records pointing to ingress (public ALB)
        # these enable public access to data plane via the internet-facing ALB
        cnames = [
            "*.svc",
            "metrics",
            "prometheus",
        ]
        for cname in cnames:
            # use default arg to capture cname value at loop time (avoid closure issue)
            aws.route53.Record(
                f"{name}-{cname.replace('*', 'wildcard').replace('.', '-')}-cname",
                zone_id=self.zone.id,
                name=fqdn.apply(lambda f, c=cname: f"{c}.{f}"),
                type="CNAME",
                records=[fqdn.apply(lambda f: f"ingress.{f}")],
                ttl=300,
                allow_overwrite=True,
                opts=child_opts,
            )

        # create ACM certificate - include *.svc subdomain for data plane endpoints
        # wildcard certs only match one level, so we need explicit *.svc.{fqdn}
        self.certificate = aws.acm.Certificate(
            f"{name}-cert",
            domain_name=fqdn.apply(lambda f: f"*.{f}"),
            subject_alternative_names=[
                fqdn,
                fqdn.apply(lambda f: f"*.svc.{f}"),
            ],
            validation_method="DNS",
            tags={**tags, "Name": f"{name}-cert"},
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.delegation],
                retain_on_delete=True,  # cert may be in use by ALBs
            ),
        )

        # create DNS validation records (one per domain in the cert)
        # ACM may reuse the same validation record for multiple domains
        validation_records = []
        for i in range(3):
            validation_record = aws.route53.Record(
                f"{name}-cert-validation-{i}",
                zone_id=self.zone.id,
                name=self.certificate.domain_validation_options[i].resource_record_name,
                type=self.certificate.domain_validation_options[i].resource_record_type,
                records=[
                    self.certificate.domain_validation_options[i].resource_record_value
                ],
                ttl=300,
                allow_overwrite=True,
                opts=child_opts,
            )
            validation_records.append(validation_record)

        # Certificate validation
        self.certificate_validation = aws.acm.CertificateValidation(
            f"{name}-cert-validation",
            certificate_arn=self.certificate.arn,
            validation_record_fqdns=[r.fqdn for r in validation_records],
            opts=pulumi.ResourceOptions(parent=self, depends_on=validation_records),
        )

        # private endpoint certificate - for PrivateLink access
        # these domains use .private suffix pattern
        private_cnames = [f"{c}.private" for c in cnames]
        self._private_dns_domains = [
            fqdn.apply(lambda f, c=c: f"{c}.{f}") for c in private_cnames
        ]

        self.private_certificate = aws.acm.Certificate(
            f"{name}-private-cert",
            domain_name=self._private_dns_domains[0],
            subject_alternative_names=self._private_dns_domains[1:],
            validation_method="DNS",
            tags={**tags, "Name": f"{name}-private-cert"},
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.delegation],
                retain_on_delete=True,
            ),
        )

        # number of unique validation records depends on domain count
        private_validation_records = []
        for i in range(len(private_cnames)):
            private_validation_record = aws.route53.Record(
                f"{name}-private-cert-validation-{i}",
                zone_id=self.zone.id,
                name=self.private_certificate.domain_validation_options[
                    i
                ].resource_record_name,
                type=self.private_certificate.domain_validation_options[
                    i
                ].resource_record_type,
                records=[
                    self.private_certificate.domain_validation_options[
                        i
                    ].resource_record_value
                ],
                ttl=300,
                allow_overwrite=True,
                opts=child_opts,
            )
            private_validation_records.append(private_validation_record)

        self.private_certificate_validation = aws.acm.CertificateValidation(
            f"{name}-private-cert-validation",
            certificate_arn=self.private_certificate.arn,
            validation_record_fqdns=[r.fqdn for r in private_validation_records],
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=private_validation_records
            ),
        )

        self._fqdn = fqdn
        self._subdomain = subdomain

        self.register_outputs(
            {
                "zone_id": self.zone.id,
                "zone_name_servers": self.zone.name_servers,
                "certificate_arn": self.certificate_validation.certificate_arn,
                "private_certificate_arn": self.private_certificate_validation.certificate_arn,
                "fqdn": fqdn,
            }
        )

    @property
    def zone_id(self) -> pulumi.Output[str]:
        return self.zone.id

    @property
    def fqdn(self) -> pulumi.Output[str]:
        return self._fqdn

    @property
    def name_servers(self) -> pulumi.Output[list]:
        return self.zone.name_servers

    @property
    def certificate_arn(self) -> pulumi.Output[str]:
        return self.certificate_validation.certificate_arn

    @property
    def subdomain(self) -> pulumi.Input[str]:
        return self._subdomain

    @property
    def private_certificate_arn(self) -> pulumi.Output[str]:
        return self.private_certificate_validation.certificate_arn

    @property
    def private_dns_domains(self) -> list[pulumi.Output[str]]:
        return self._private_dns_domains
