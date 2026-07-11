"""Tests for eval.ground_truth — Tier-A generator + Tier-B loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from java_codebase_rag.eval.ground_truth import (
    LabeledQuery,
    build_tier_a,
    load_tier_b,
)


class _Sym:
    """Minimal structural symbol stand-in (duck-typed .fqn / .name)."""

    def __init__(self, fqn: str, name: str) -> None:
        self.fqn = fqn
        self.name = name


class TestBuildTierA:
    def test_build_tier_a_deterministic(self) -> None:
        syms = [
            _Sym("com.example.DistributionChunkService", "DistributionChunkService"),
            _Sym("com.example.Other", "Other"),
        ]
        out = build_tier_a(syms)

        fqn = "com.example.DistributionChunkService"
        assert LabeledQuery("DistributionChunkService", frozenset({fqn}), "A") in out
        assert LabeledQuery(
            "distribution chunk service", frozenset({fqn}), "A"
        ) in out

        # Deterministic: same input -> identical output
        assert build_tier_a(syms) == out

        # Sorted by (query, fqn)
        keys = [(q.query, next(iter(q.relevant))) for q in out]
        assert keys == sorted(keys)

    def test_build_tier_a_skips_noise(self) -> None:
        syms = [
            _Sym("com.example.A", "A"),  # <3 chars
            _Sym("com.example.Do", "Do"),  # splits to single token
        ]
        assert build_tier_a(syms) == []


class TestLoadTierB:
    def test_load_tier_b_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "tier_b.yaml"
        path.write_text(
            "- query: distribution chunk service\n"
            "  relevant:\n"
            "    - com.example.DistributionChunkService\n"
            "- query: user service\n"
            "  relevant:\n"
            "    - com.example.UserService\n"
            "    - com.other.UserService\n"
        )
        out = load_tier_b(path)
        assert out == [
            LabeledQuery(
                "distribution chunk service",
                frozenset({"com.example.DistributionChunkService"}),
                "B",
            ),
            LabeledQuery(
                "user service",
                frozenset({"com.example.UserService", "com.other.UserService"}),
                "B",
            ),
        ]

    def test_load_tier_b_json(self, tmp_path: Path) -> None:
        path = tmp_path / "tier_b.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "query": "distribution chunk service",
                        "relevant": ["com.example.DistributionChunkService"],
                    },
                    {
                        "query": "user service",
                        "relevant": [
                            "com.example.UserService",
                            "com.other.UserService",
                        ],
                    },
                ]
            )
        )
        out = load_tier_b(path)
        assert out == [
            LabeledQuery(
                "distribution chunk service",
                frozenset({"com.example.DistributionChunkService"}),
                "B",
            ),
            LabeledQuery(
                "user service",
                frozenset({"com.example.UserService", "com.other.UserService"}),
                "B",
            ),
        ]

    def test_load_tier_b_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_tier_b(tmp_path / "does_not_exist.yaml")
