from app.adapters.base import ProviderAdapter
from app.adapters.mock_train import MockTrainAdapter
from app.adapters.registry import (
    DuplicateProviderIdError,
    InvalidProviderIdError,
    ProviderRegistry,
    UnknownProviderError,
)

provider_registry = ProviderRegistry([MockTrainAdapter()])

__all__ = [
    "DuplicateProviderIdError",
    "InvalidProviderIdError",
    "ProviderAdapter",
    "ProviderRegistry",
    "UnknownProviderError",
    "get_adapter",
    "provider_registry",
]


def get_adapter(provider_id: str) -> ProviderAdapter:
    return provider_registry.get(provider_id)
