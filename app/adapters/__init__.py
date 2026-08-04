from app.adapters.base import ProviderAdapter
from app.adapters.http_train import HttpTrainAdapter
from app.adapters.mock_train import MockTrainAdapter
from app.adapters.registry import (
    DuplicateProviderIdError,
    InvalidProviderIdError,
    ProviderRegistry,
    UnknownProviderError,
)
from app.core.config import Settings, settings


def build_provider_registry(config: Settings) -> ProviderRegistry:
    configured_adapters: list[ProviderAdapter] = [MockTrainAdapter()]
    if config.train_provider_base_url is not None:
        configured_adapters.append(
            HttpTrainAdapter(
                base_url=config.train_provider_base_url,
                bearer_token=(
                    config.train_provider_bearer_token.get_secret_value()
                    if config.train_provider_bearer_token is not None
                    else None
                ),
                timeout_seconds=config.train_provider_timeout_seconds,
            )
        )
    return ProviderRegistry(configured_adapters)


provider_registry = build_provider_registry(settings)

__all__ = [
    "DuplicateProviderIdError",
    "InvalidProviderIdError",
    "HttpTrainAdapter",
    "ProviderAdapter",
    "ProviderRegistry",
    "UnknownProviderError",
    "build_provider_registry",
    "get_adapter",
    "provider_registry",
]


def get_adapter(provider_id: str) -> ProviderAdapter:
    return provider_registry.get(provider_id)
