"""Base configuration for BYOC infrastructure (cloud-agnostic)."""

import re

from pydantic import BaseModel, Field


class NodePoolTaint(BaseModel):
    key: str
    value: str
    effect: str = "NO_SCHEDULE"


ARM_FAMILIES = re.compile(r"^(?:[a-z]+\d+g[a-z]*\.|c4a-|t2a-|n4a-|Standard_[A-Z]\d+p[a-z]*_v\d)")


def node_arch(instance_type: str) -> str:
    """`arm64` for Graviton (`m8g.*`), Axion/Ampere (`c4a-`, `t2a-`) and Azure `p` sizes (`Standard_D4ps_v6`)."""
    return "arm64" if ARM_FAMILIES.match(instance_type) else "amd64"


class NodePoolConfig(BaseModel):
    """Shared node pool config. Each cloud reads its own field: instance_type (AWS), machine_type (GCP), vm_size (Azure)."""

    name: str
    # AWS-only
    instance_type: str = "r6in.large"
    desired_size: int = 3
    disk_type: str = "gp3"
    # GCP-only
    machine_type: str = "n2-standard-4"
    # Azure-only
    vm_size: str = "Standard_D4s_v5"
    # Common
    min_size: int = 1
    max_size: int = 10
    disk_size_gb: int = 100
    labels: dict[str, str] = Field(default_factory=dict)
    taints: list[NodePoolTaint] = Field(default_factory=list)

    def size_for(self, cloud: str) -> str:
        return {"aws": self.instance_type, "gcp": self.machine_type, "azure": self.vm_size}[cloud]


def default_node_arch(node_pools: list[NodePoolConfig], cloud: str) -> str:
    """Architecture of the `default` pool, which hosts every binpacked workload."""
    default = next((np for np in node_pools if np.name == "default"), node_pools[0])
    return node_arch(default.size_for(cloud))


class BaseConfig(BaseModel):
    region: str
    global_env: str = "prod"
    cloud: str = "aws"

    availability_zones: list[str]
    vpc_cidr: str = "10.0.0.0/16"
    kubernetes_version: str = "1.35"
    node_pools: list[NodePoolConfig] = Field(default_factory=list)

    @property
    def resource_prefix(self) -> str:
        return "pc"
