"""Regression test: ``parse_java`` must be safe to call from multiple threads.

``ast_java._parser()`` returns a **per-thread** tree-sitter ``Parser`` because
``Parser.parse()`` mutates internal parser state and is not thread-safe on a
shared instance. ``parse_java`` is now reached concurrently from worker threads
when indexing runs with cocoindex's inflight parallelism (both directly from
``process_java_file`` and transitively from ``enrich_chunk`` →
``collect_annotation_meta_chain``). This test locks that invariant in: a future
change that reverts to a single shared ``Parser`` would corrupt parses here
(wrong counts / ``parse_error`` / native crash).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from java_codebase_rag.ast.ast_java import parse_java

_SRC_A = b"""
package com.example.alpha;

import java.util.List;

public class Alpha {
    private final Beta beta;
    public Alpha(Beta beta) { this.beta = beta; }
    public void run(int n) {
        for (int i = 0; i < n; i++) { beta.handle(i); }
    }
}
"""

_SRC_B = b"""
package com.example.beta;

public class Beta {
    public void handle(int x) { System.out.println(x); }
    protected int compute(long a, long b) { return (int)(a + b); }
}
"""


def _facts(src: bytes) -> tuple[str, int, int]:
    """Stable structural fingerprint: (package, #types, #methods)."""
    ast = parse_java(src)
    methods = sum(len(t.methods) for t in ast.all_types)
    return (ast.package, len(ast.all_types), methods)


def test_parse_java_concurrent_matches_single_threaded() -> None:
    ref_a = _facts(_SRC_A)
    ref_b = _facts(_SRC_B)
    # Loose sanity: the single-threaded references must be non-trivial and
    # distinct, so the equality check below is actually exercising something.
    assert ref_a[1] >= 1 and ref_a[2] >= 1
    assert ref_b[1] >= 1 and ref_b[2] >= 1
    assert ref_a != ref_b

    # 16 threads each parse both sources 60×; every result must match the
    # single-threaded reference. A shared Parser would corrupt some parses.
    def worker() -> bool:
        return all(_facts(_SRC_A) == ref_a and _facts(_SRC_B) == ref_b for _ in range(60))

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(lambda _: worker(), range(16)))

    assert all(results)
