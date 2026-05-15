locals {
  resource_prefix = "pc"
  dns_cnames      = ["*.svc", "metrics", "prometheus"]

  org_slug        = substr(replace(lower(pineconebyoc_environment.this.org_name), "/[^a-z0-9]/", ""), 0, 16)
  env_first_label = split(".", pineconebyoc_environment.this.env_name)[0]
  env_suffix      = substr(local.env_first_label, length(local.env_first_label) - 4, 4)
  cell_name       = "${local.org_slug}-byoc-${local.env_suffix}"
  cell_org        = split("-byoc-", local.cell_name)[0]
  cell_sa_suffix  = "-byoc-${local.env_suffix}"
  resource_suffix = substr(local.cell_name, length(local.cell_name) - 4, 4)
  subdomain       = trimsuffix(pineconebyoc_environment.this.env_name, ".byoc")
  fqdn            = "${local.subdomain}.${var.parent_dns_zone_name}"

  labels = merge({
    "pinecone-managed-by" = "pulumi"
  }, var.labels)

  registry_base   = "us-docker.pkg.dev/pinecone-artifacts/unstable"
  pinetools_image = "${local.registry_base}/pinetools:${var.pinecone_version}"
  cluster_name    = "cluster-${local.cell_name}"

  np_sa_account_id      = "np-${substr(local.cell_org, 0, max(0, 30 - 2 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  reader_sa_account_id  = "read-${substr(local.cell_org, 0, max(0, 30 - 4 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  writer_sa_account_id  = "write-${substr(local.cell_org, 0, max(0, 30 - 5 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  dns_sa_account_id     = "dns-${substr(local.cell_org, 0, max(0, 30 - 3 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  pulumi_sa_account_id  = "pulumi-${substr(local.cell_org, 0, max(0, 30 - 6 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"
  storage_sa_account_id = "si-${substr(local.cell_org, 0, max(0, 30 - 2 - 1 - length(local.cell_sa_suffix)))}${local.cell_sa_suffix}"

  kubeconfig = <<-YAML
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${google_container_cluster.this.master_auth[0].cluster_ca_certificate}
    server: https://${google_container_cluster.this.endpoint}
  name: ${google_container_cluster.this.name}
contexts:
- context:
    cluster: ${google_container_cluster.this.name}
    user: ${google_container_cluster.this.name}
  name: ${google_container_cluster.this.name}
current-context: ${google_container_cluster.this.name}
kind: Config
preferences: {}
users:
- name: ${google_container_cluster.this.name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      installHint: Install gke-gcloud-auth-plugin for use with kubectl
      provideClusterInfo: true
YAML

  dbs = {
    control = {
      name      = "control-db"
      db_name   = "controldb"
      username  = "controldb"
      cpu_count = 2
    }
    system = {
      name      = "system-db"
      db_name   = "systemdb"
      username  = "systemdb"
      cpu_count = 2
    }
  }
}
