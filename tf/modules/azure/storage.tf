resource "azurerm_storage_account" "this" {
  name                            = local.storage_account_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = var.region
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  access_tier                     = "Hot"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 3
    }
  }
}

resource "azurerm_storage_container" "this" {
  for_each = toset(["data", "wal", "index-backups", "janitor", "internal"])

  name                  = "pc-${each.value}-${local.cell_name}"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  rule {
    name    = "delete-old-versions"
    enabled = true
    filters {
      blob_types = ["blockBlob", "appendBlob"]
    }
    actions {
      version {
        delete_after_days_since_creation = 3
      }
    }
  }

  rule {
    name    = "delete-activity-scrapes"
    enabled = true
    filters {
      blob_types   = ["blockBlob", "appendBlob"]
      prefix_match = ["${azurerm_storage_container.this["data"].name}/activity-scrapes/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 30
      }
    }
  }

  rule {
    name    = "delete-janitor"
    enabled = true
    filters {
      blob_types   = ["blockBlob", "appendBlob"]
      prefix_match = ["${azurerm_storage_container.this["janitor"].name}/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 7
      }
    }
  }

  rule {
    name    = "delete-lag-reporter"
    enabled = true
    filters {
      blob_types   = ["blockBlob", "appendBlob"]
      prefix_match = ["${azurerm_storage_container.this["internal"].name}/lag-reporter/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 14
      }
    }
  }
}
