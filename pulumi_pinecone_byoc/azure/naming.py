"""Azure resource naming helpers.

Globally-unique Azure resource names (storage accounts, key vaults) must fit
within 24 characters. These helpers keep the unique '-byoc-XXXX' suffix intact
and truncate the org portion.
"""

_STORAGE_ACCOUNT_MAX = 24  # lowercase alphanumeric only
_KEY_VAULT_MAX = 24  # alphanumeric and hyphens


def storage_account_name(prefix: str, cell_name: str) -> str:
    flat = cell_name.replace("-", "")
    suffix_start = flat.find("byoc")
    suffix = flat[suffix_start:]  # "byocXXXX"
    org = flat[:suffix_start]
    max_org = _STORAGE_ACCOUNT_MAX - len(prefix) - len(suffix)
    return f"{prefix}{org[:max_org]}{suffix}"


def key_vault_name(prefix: str, cell_name: str) -> str:
    suffix_start = cell_name.rfind("-byoc-")
    suffix = cell_name[suffix_start:]  # "-byoc-XXXX"
    org = cell_name[:suffix_start]
    max_org = _KEY_VAULT_MAX - len(prefix) - 1 - len(suffix)  # -1 for separator
    return f"{prefix}-{org[:max_org]}{suffix}"
