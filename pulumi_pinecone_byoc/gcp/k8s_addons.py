"""K8s addons for GCP infrastructure."""

import pulumi
import pulumi_kubernetes as k8s

from .gke import GKE


class K8sAddons(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        gke: GKE,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pinecone:byoc:K8sAddons", name, None, opts)

        self.gloo_namespace = k8s.core.v1.Namespace(
            f"{name}-gloo-system",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="gloo-system",
                labels={
                    "kubernetes.io/metadata.name": "gloo-system",
                    "name": "gloo-system",
                },
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=gke.k8s_provider),
        )

        self.register_outputs(
            {
                "gloo_namespace": self.gloo_namespace.metadata.name,
            }
        )
