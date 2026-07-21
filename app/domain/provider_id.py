def normalize_provider_id(value: str | None) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("Provider ID must be a non-empty string")
    return normalized_value
