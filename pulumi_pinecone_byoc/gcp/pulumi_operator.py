"""
Pulumi Kubernetes Operator setup for GCP.

Creates GCS backend, Cloud KMS key for secrets, and IAM bindings for the
GKE pulumi service account.
"""

from typing import Optional

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s

from config.gcp import GCPConfig


class PulumiOperator(pulumi.ComponentResource):
    """
    Sets up pulumi-k8s-operator to use GCS backend instead of Pulumi Cloud.

    Creates:
    - GCS bucket for Pulumi state storage
    - Cloud KMS key for encrypting Pulumi secrets
    - IAM bindings for the GKE pulumi service account (bucket + KMS)

    Note: The GCP service account is created by GKE (passed via pulumi_sa_email),
    NOT created here.
    """

    def __init__(
        self,
        name: str,
        config: GCPConfig,
        k8s_provider: k8s.Provider,
        pulumi_sa_email: pulumi.Output[str],
        cell_name: pulumi.Input[str],
        operator_namespace: str = "pulumi-kubernetes-operator",
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        super().__init__("pinecone:byoc:PulumiOperator", name, None, opts)

        self.config = config
        self._cell_name = pulumi.Output.from_input(cell_name)
        child_opts = pulumi.ResourceOptions(parent=self)
        self._state_bucket = self._create_state_bucket(name, child_opts)
        self._kms_key = self._create_kms_key(name, child_opts)
        self._create_iam_bindings(name, pulumi_sa_email, child_opts)
        self._backend_url = self._state_bucket.name.apply(
            lambda bucket: f"gs://{bucket}"
        )
        self._secrets_provider = self._kms_key.id.apply(
            lambda key_id: f"gcpkms://{key_id}"
        )

        self.register_outputs(
            {
                "state_bucket_name": self._state_bucket.name,
                "kms_key_id": self._kms_key.id,
                "backend_url": self._backend_url,
                "secrets_provider": self._secrets_provider,
            }
        )

    def _create_state_bucket(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> gcp.storage.Bucket:
        """Create GCS bucket for Pulumi state storage."""
        bucket_name = self._cell_name.apply(lambda cn: f"pc-pulumi-state-{cn}")

        bucket = gcp.storage.Bucket(
            f"{name}-state-bucket",
            name=bucket_name,
            project=self.config.gcp_project,
            location=self.config.region,
            force_destroy=True,
            uniform_bucket_level_access=True,
            versioning=gcp.storage.BucketVersioningArgs(
                enabled=True,
            ),
            lifecycle_rules=[
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(
                        type="AbortIncompleteMultipartUpload",
                    ),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        age=2,
                    ),
                ),
                gcp.storage.BucketLifecycleRuleArgs(
                    action=gcp.storage.BucketLifecycleRuleActionArgs(
                        type="Delete",
                    ),
                    condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                        num_newer_versions=30,
                        with_state="ARCHIVED",
                    ),
                ),
            ],
            labels=self.config.labels(),
            opts=opts,
        )

        return bucket

    def _create_kms_key(
        self, name: str, opts: pulumi.ResourceOptions
    ) -> gcp.kms.CryptoKey:
        """Create Cloud KMS key for encrypting Pulumi secrets."""
        key_ring = gcp.kms.KeyRing(
            f"{name}-pulumi-secrets-keyring",
            name=self._cell_name.apply(lambda cn: f"pulumi-secrets-{cn}"),
            project=self.config.gcp_project,
            location=self.config.region,
            opts=opts,
        )

        key = gcp.kms.CryptoKey(
            f"{name}-pulumi-secrets-key",
            name="pulumi-secrets",
            key_ring=key_ring.id,
            rotation_period="7776000s",  # 90 days
            purpose="ENCRYPT_DECRYPT",
            opts=opts,
        )

        return key

    def _create_iam_bindings(
        self,
        name: str,
        pulumi_sa_email: pulumi.Output[str],
        opts: pulumi.ResourceOptions,
    ):
        """Create IAM bindings for the GKE pulumi service account."""
        gcp.storage.BucketIAMMember(
            f"{name}-state-bucket-access",
            bucket=self._state_bucket.name,
            role="roles/storage.objectAdmin",
            member=pulumi_sa_email.apply(lambda e: f"serviceAccount:{e}"),
            opts=opts,
        )

        gcp.kms.CryptoKeyIAMMember(
            f"{name}-kms-key-access",
            crypto_key_id=self._kms_key.id,
            role="roles/cloudkms.cryptoKeyEncrypterDecrypter",
            member=pulumi_sa_email.apply(lambda e: f"serviceAccount:{e}"),
            opts=opts,
        )

    @property
    def backend_url(self) -> pulumi.Output[str]:
        """GCS backend URL for Stack CRD spec.backend field."""
        return self._backend_url

    @property
    def secrets_provider(self) -> pulumi.Output[str]:
        """Cloud KMS secrets provider URL for Stack CRD spec.secretsProvider field."""
        return self._secrets_provider

    @property
    def kms_key_id(self) -> pulumi.Output[str]:
        return self._kms_key.id

    @property
    def namespace(self) -> str:
        return "pulumi-kubernetes-operator"
