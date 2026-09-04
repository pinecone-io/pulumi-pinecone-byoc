"""K8s addons for GCP infrastructure."""

import pulumi

from ..common.gloo import gloo_namespace
from .gke import GKE


class K8sAddons(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        gke: GKE,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:K8sAddons", name, None, opts)

        self.gloo_namespace = gloo_namespace(
            name, gke.k8s_provider, pulumi.ResourceOptions(parent=self)
        )

        self.register_outputs(
            {
                "gloo_namespace": self.gloo_namespace.metadata.name,
            }
        )
