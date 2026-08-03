PRIVATE_REVALIDATION_CACHE_CONTROL = "private, no-cache"


def if_none_match_matches(if_none_match: str | None, current_etag: str) -> bool:
    """Apply the weak entity-tag comparison required by If-None-Match."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == current_etag:
            return True
    return False
