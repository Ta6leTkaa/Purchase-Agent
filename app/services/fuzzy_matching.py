import re
from difflib import SequenceMatcher


def fuzzy_text_score(query: str, candidate: str) -> float:
    """Score punctuation-insensitive token and morphological similarity."""
    normalized_query = normalize_fuzzy_text(query)
    normalized_candidate = normalize_fuzzy_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_query == normalized_candidate:
        return 1.0
    query_tokens = set(normalized_query.split())
    candidate_tokens = set(normalized_candidate.split())
    if query_tokens <= candidate_tokens:
        return 0.94
    overlap = len(query_tokens & candidate_tokens) / len(query_tokens)
    morphological_overlap = sum(
        max(
            SequenceMatcher(None, query_token, candidate_token).ratio()
            for candidate_token in candidate_tokens
        )
        >= 0.78
        for query_token in query_tokens
    ) / len(query_tokens)
    sequence = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    return max(overlap * 0.88, morphological_overlap * 0.9, sequence)


def normalize_fuzzy_text(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold().replace("ё", "е")))
