resource "terraform_data" "storage_integration_deleted_object_purge" {
  input = {
    display_name = "${local.cell_name}-storage-integration"
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu

      display_name='${self.input.display_name}'
      absent_deadline=$((SECONDS + 30))
      deadline=$((SECONDS + 180))
      seen_deleted_object=0

      deleted_ids() {
        object_type="$1"
        az rest \
          --method get \
          --url "https://graph.microsoft.com/v1.0/directory/deletedItems/microsoft.graph.$object_type?\$filter=displayName%20eq%20'$display_name'&\$select=id,displayName" \
          --query "value[?displayName=='$display_name'].id" \
          --output tsv 2>/dev/null || true
      }

      purge_deleted_objects() {
        object_type="$1"
        ids="$(deleted_ids "$object_type")"
        [ -n "$ids" ] || return 0

        seen_deleted_object=1
        printf '%s\n' "$ids" | while IFS= read -r object_id; do
          [ -n "$object_id" ] || continue
          az rest --method delete --url "https://graph.microsoft.com/v1.0/directory/deletedItems/$object_id" >/dev/null
        done
      }

      has_deleted_objects() {
        for object_type in application servicePrincipal; do
          if [ -n "$(deleted_ids "$object_type")" ]; then
            return 0
          fi
        done
        return 1
      }

      while :; do
        purge_deleted_objects application
        purge_deleted_objects servicePrincipal

        if ! has_deleted_objects; then
          if [ "$seen_deleted_object" = "1" ] || [ "$SECONDS" -ge "$absent_deadline" ]; then
            exit 0
          fi
        fi

        if [ "$SECONDS" -gt "$deadline" ]; then
          echo "Timed out purging deleted Entra storage integration objects for $display_name" >&2
          exit 1
        fi

        sleep 5
      done
    EOT
  }
}

resource "azuread_application" "storage_integration" {
  display_name = "${local.cell_name}-storage-integration"

  depends_on = [terraform_data.storage_integration_deleted_object_purge]
}

resource "azuread_service_principal" "storage_integration" {
  client_id = azuread_application.storage_integration.client_id
}

resource "azuread_service_principal_password" "storage_integration" {
  service_principal_id = azuread_service_principal.storage_integration.id
}

resource "azurerm_role_assignment" "storage_integration_reader" {
  scope              = "/subscriptions/${var.subscription_id}"
  role_definition_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
  principal_id       = azuread_service_principal.storage_integration.object_id
  principal_type     = "ServicePrincipal"
}
