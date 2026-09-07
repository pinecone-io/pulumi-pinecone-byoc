output "cluster_name" {
  value = module.pinecone.cluster_name
}

output "region" {
  value = module.pinecone.region
}

output "organization_id" {
  value = module.pinecone.organization_id
}

output "organization_name" {
  value = module.pinecone.organization_name
}

output "environment_id" {
  value = module.pinecone.environment_id
}

output "environment_name" {
  value = module.pinecone.environment_name
}

output "vnet_id" {
  value = module.pinecone.vnet_id
}

output "kubeconfig" {
  value     = module.pinecone.kubeconfig
  sensitive = true
}

output "storage_account_name" {
  value = module.pinecone.storage_account_name
}

output "control_db_endpoint" {
  value = module.pinecone.control_db_endpoint
}

output "system_db_endpoint" {
  value = module.pinecone.system_db_endpoint
}

output "pulumi_backend_url" {
  value = module.pinecone.pulumi_backend_url
}

output "pulumi_secrets_provider" {
  value = module.pinecone.pulumi_secrets_provider
}

output "private_link_service_name" {
  value = module.pinecone.private_link_service_name
}

output "private_link_service_resource_group" {
  value = module.pinecone.private_link_service_resource_group
}
