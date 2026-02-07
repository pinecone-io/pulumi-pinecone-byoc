from .base import BaseConfig, NodePoolConfig, NodePoolTaint

# AWS config (backwards compatible alias)
from .aws import Config, DatabaseConfig, DatabaseInstanceConfig

# GCP config
from .gcp import GCPConfig, AlloyDBConfig, AlloyDBInstanceConfig

__all__ = [
    # Base
    "BaseConfig",
    "NodePoolConfig",
    "NodePoolTaint",
    # AWS
    "Config",
    "DatabaseConfig",
    "DatabaseInstanceConfig",
    # GCP
    "GCPConfig",
    "AlloyDBConfig",
    "AlloyDBInstanceConfig",
]
