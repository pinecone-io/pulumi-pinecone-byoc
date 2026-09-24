resource "kubernetes_namespace_v1" "gloo_system" {
  metadata {
    name = "gloo-system"
    labels = {
      "kubernetes.io/metadata.name" = "gloo-system"
      name                          = "gloo-system"
    }
  }

  depends_on = [google_container_node_pool.this]
}
