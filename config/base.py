"""Base configuration for BYOC infrastructure (cloud-agnostic)."""

from pydantic import BaseModel, Field


class NodePoolTaint(BaseModel):
    key: str
    value: str
    effect: str = "NO_SCHEDULE"


class NodePoolConfig(BaseModel):
    """Shared node pool config. Each cloud reads its own field: instance_type (AWS), machine_type (GCP), vm_size (Azure)."""

    name: str
    # AWS-only
    instance_type: str = "r6in.large"
    disk_type: str = "gp3"
    # GCP-only
    machine_type: str = "n2-standard-4"
    # Azure-only
    vm_size: str = "Standard_D4s_v5"
    # Common
    min_size: int = 1
    max_size: int = 12
    desired_size: int = 3
    disk_size_gb: int = 100
    labels: dict[str, str] = Field(default_factory=dict)
    taints: list[NodePoolTaint] = Field(default_factory=list)

    def initial_count(self) -> int:
        """Nodes the pool is born with, a total across its zones."""
        return max(self.min_size, min(self.desired_size, self.max_size))

    def initial_count_per_zone(self, zones: int) -> int:
        """`initial_count` for a cloud that seeds a pool per zone even where its
        limits are totals, as GKE does, and so cannot seed more than max_size."""
        zones = max(zones, 1)
        return max(1, min(-(-self.initial_count() // zones), self.max_size // zones))


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
