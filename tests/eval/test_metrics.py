"""Tests for eval.metrics — hand-computed cases to verify metric math."""

import pytest
from java_codebase_rag.eval.metrics import (
    recall_at_k,
    precision_at_k,
    reciprocal_rank,
    mean,
    aggregate,
)


class TestRecallAtK:
    def test_recall_at_k_basic(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b", "d"}
        k = 3
        assert recall_at_k(retrieved, relevant, k) == 0.5  # b found, d not

    def test_recall_at_k_small_k(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b", "d"}
        k = 1
        assert recall_at_k(retrieved, relevant, k) == 0.0  # b not in first position

    def test_recall_at_k_empty_relevant(self):
        retrieved = ["a", "b", "c"]
        relevant = set()
        k = 3
        assert recall_at_k(retrieved, relevant, k) == 0.0  # empty relevant set

    def test_recall_at_k_k_larger_than_retrieved(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b", "d"}
        k = 10  # longer than retrieved
        assert recall_at_k(retrieved, relevant, k) == 0.5  # only b found


class TestPrecisionAtK:
    def test_precision_at_k_basic(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b"}
        k = 2
        assert precision_at_k(retrieved, relevant, k) == 0.5  # 1 out of 2

    def test_precision_at_k_full_retrieved(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b"}
        k = 3
        assert precision_at_k(retrieved, relevant, k) == 1.0 / 3.0  # 1 out of 3

    def test_precision_at_k_zero_k(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b"}
        k = 0
        assert precision_at_k(retrieved, relevant, k) == 0.0  # k == 0


class TestReciprocalRank:
    def test_reciprocal_rank_second_position(self):
        retrieved = ["a", "b", "c"]
        relevant = {"b"}
        assert reciprocal_rank(retrieved, relevant) == 0.5  # 1/2

    def test_reciprocal_rank_no_match(self):
        retrieved = ["a", "b", "c"]
        relevant = {"z"}
        assert reciprocal_rank(retrieved, relevant) == 0.0  # no match

    def test_reciprocal_rank_first_position(self):
        retrieved = ["a", "b", "c"]
        relevant = {"a"}
        assert reciprocal_rank(retrieved, relevant) == 1.0  # 1/1


class TestMean:
    def test_mean_basic(self):
        values = [1.0, 0.0, 0.5]
        assert mean(values) == 0.5  # (1.0 + 0.0 + 0.5) / 3

    def test_mean_empty(self):
        values = []
        assert mean(values) == 0.0  # empty list


class TestAggregate:
    def test_aggregate_basic(self):
        per_query = [
            {"recall@1": 1.0, "recall@5": 1.0, "recall@10": 1.0, "recall@20": 1.0, "precision@5": 1.0, "mrr": 1.0},
            {"recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0, "recall@20": 0.0, "precision@5": 0.0, "mrr": 0.0},
        ]
        result = aggregate(per_query)
        assert result == {
            "recall@1": 0.5,
            "recall@5": 0.5,
            "recall@10": 0.5,
            "recall@20": 0.5,
            "precision@5": 0.5,
            "mrr": 0.5,
        }
