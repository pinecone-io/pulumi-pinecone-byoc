"""The gloo-system namespace, created identically on every cloud."""

import pulumi
import pulumi_kubernetes as k8s


def gloo_namespace(
    name: str,
    k8s_provider: pulumi.ProviderResource,
    opts: pulumi.ResourceOptions,
) -> k8s.core.v1.Namespace:
    # retain on delete: residual Gloo CRD instances + admission webhooks hold finalizers
    # that block namespace termination, and cluster teardown reaps the namespace anyway.
    # Safe only while this module owns the cluster: left in one that outlives the stack -
    # adopted, or `destroy --target` here - the namespace stays and the next up cannot
    # create it.
    return k8s.core.v1.Namespace(
        f"{name}-gloo-system",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="gloo-system",
            labels={
                "kubernetes.io/metadata.name": "gloo-system",
                "name": "gloo-system",
            },
        ),
        opts=pulumi.ResourceOptions.merge(
            opts,
            pulumi.ResourceOptions(provider=k8s_provider, retain_on_delete=True),
        ),
    )
