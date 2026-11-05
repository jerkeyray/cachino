"""A deliberately flawed cache report for a read-only inspection task."""


def cache_ratio(rows):
    """Return a ratio for requests containing provider usage."""
    prompt = sum(row["prompt_tokens"] for row in rows)
    cached = sum(row.get("cached_tokens", 0) or 0 for row in rows)
    return cached / prompt if prompt else 0
