"""Container registry configuration per cloud provider."""

from dataclasses import dataclass
from typing import Literal

PINETOOLS_TAG = "latest"

RegistryType = Literal["ecr", "gcr"]


@dataclass(frozen=True)
class ContainerRegistry:
    base_url: str
    type: RegistryType

    @property
    def pinetools_image(self) -> str:
        return f"{self.base_url}/pinetools:{PINETOOLS_TAG}"


AWS_REGISTRY = ContainerRegistry(
    base_url="843333058014.dkr.ecr.us-east-1.amazonaws.com/unstable/pinecone/v4",
    type="ecr",
)

GCP_REGISTRY = ContainerRegistry(
    base_url="us-docker.pkg.dev/pinecone-artifacts/unstable",
    type="gcr",
)
