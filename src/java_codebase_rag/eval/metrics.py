"""IR evaluation metrics — pure functions, stdlib only.

Functions take `retrieved: list[str]` (ordered list of retrieved FQN ids)
and `relevant: set[str]` (ground-truth relevant set).
"""

from __future__ import annotations


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant documents appearing in retrieved[:k].

    Args:
        retrieved: Ordered list of retrieved document IDs.
        relevant: Set of ground-truth relevant document IDs.
        k: Cut-off rank (1-indexed).

    Returns:
        Recall@k in [0.0, 1.0]. Returns 0.0 if relevant is empty.
    """
    if not relevant:
        return 0.0

    retrieved_at_k = set(retrieved[:k])
    relevant_retrieved = retrieved_at_k.intersection(relevant)

    return len(relevant_retrieved) / len(relevant)


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Precision at cut-off k: |retrieved[:k] ∩ relevant| / k.

    Args:
        retrieved: Ordered list of retrieved document IDs.
        relevant: Set of ground-truth relevant document IDs.
        k: Cut-off rank (1-indexed).

    Returns:
        Precision@k in [0.0, 1.0]. Returns 0.0 if k == 0.
    """
    if k == 0:
        return 0.0

    retrieved_at_k = set(retrieved[:k])
    relevant_retrieved = retrieved_at_k.intersection(relevant)

    return len(relevant_retrieved) / k


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """Reciprocal rank: 1.0 / rank of first retrieved relevant document.

    Args:
        retrieved: Ordered list of retrieved document IDs.
        relevant: Set of ground-truth relevant document IDs.

    Returns:
        Reciprocal rank in [0.0, 1.0]. Returns 0.0 if no relevant document
        is retrieved. Ranks are 1-indexed (first hit = 1.0).
    """
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant:
            return 1.0 / rank

    return 0.0


def mean(values: list[float]) -> float:
    """Arithmetic mean of a list of floats.

    Args:
        values: List of float values.

    Returns:
        Arithmetic mean. Returns 0.0 for empty list.
    """
    if not values:
        return 0.0

    return sum(values) / len(values)


def aggregate(per_query: list[dict]) -> dict[str, float]:
    """Aggregate per-query metrics into means across all queries.

    Takes a list of per-query metric dicts and computes the mean of each
    metric across all queries. All dicts must have the same keys.

    Args:
        per_query: List of dicts, each containing metric names as keys
            and float values (e.g., {"recall@10": 1.0, "mrr": 0.5, ...}).

    Returns:
        Dict with same keys as input, containing mean of each metric
        across all queries (e.g., {"recall@10": 0.42, "mrr": 0.55, ...}).
    """
    if not per_query:
        return {}

    metric_names = list(per_query[0].keys())
    result: dict[str, float] = {}

    for metric in metric_names:
        values = [query_metrics[metric] for query_metrics in per_query]
        result[metric] = mean(values)

    return result
