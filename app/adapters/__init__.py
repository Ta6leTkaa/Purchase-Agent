from app.adapters.base import ProviderAdapter
from app.adapters.mock_train import MockTrainAdapter


def get_adapter(provider_id: str) -> ProviderAdapter:
    if provider_id == MockTrainAdapter.provider_id:
        return MockTrainAdapter()
    raise ValueError(f"Unknown provider: {provider_id}")
