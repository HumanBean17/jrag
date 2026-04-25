"""Hybrid RRF: fuse vector hits (LanceDB chunks) with graph-derived context rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _norm_filename_key(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    p = Path(s)
    return p.as_posix().lstrip("./")


def _merge_file_group(grp: list[dict[str, Any]]) -> dict[str, Any]:
    max_score = max(float(r.get("_rrf_score", 0.0)) for r in grp)
    sources: list[str] = []
    for r in grp:
        src = r.get("_source", "")
        if src and src not in sources:
            sources.append(str(src))
    vec = [r for r in grp if r.get("_source") == "vector"]
    gr = [r for r in grp if r.get("_source") == "graph"]
    text = ""
    for r in sorted(grp, key=lambda x: float(x.get("_rrf_score", 0.0)), reverse=True):
        if r.get("_source") == "vector" and r.get("text"):
            text = str(r.get("text", ""))
            break
    if not text:
        for r in gr:
            if r.get("text"):
                text = str(r.get("text", ""))
                break
    filename = next((str(r.get("filename", "")) for r in grp if r.get("filename")), "")
    if not filename and gr:
        filename = str(gr[0].get("filename", ""))
    out: dict[str, Any] = {
        "filename": filename,
        "text": text,
        "_rrf_score": max_score,
        "_sources": sources,
        "_source": "merged" if len(sources) > 1 else (sources[0] if sources else "unknown"),
    }
    if vec and "_kind" in vec[0]:
        out["_kind"] = vec[0].get("_kind")
    if len(gr) == 1:
        g0 = gr[0]
        for k in ("fqn", "edge", "file_key", "context_id"):
            if g0.get(k) is not None:
                out[k] = g0.get(k)
    elif len(gr) > 1:
        fqs = [g.get("fqn") for g in gr if g.get("fqn") is not None]
        eds = [g.get("edge") for g in gr if g.get("edge") is not None]
        if fqs:
            out["graph_fqns"] = fqs
        if eds:
            out["graph_edges"] = eds
        if gr[0].get("file_key") is not None:
            out["file_key"] = gr[0].get("file_key")
    return out


def _dedupe_merged_by_filename(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    unkeyed: list[dict[str, Any]] = []
    for r in merged:
        nk = _norm_filename_key(str(r.get("filename", "")))
        if not nk:
            unkeyed.append(r)
            continue
        by_key.setdefault(nk, []).append(r)
    out: list[dict[str, Any]] = []
    for _nk, grp in by_key.items():
        if len(grp) == 1:
            out.append(grp[0])
        else:
            out.append(_merge_file_group(grp))
    out.extend(unkeyed)
    out.sort(key=lambda r: float(r.get("_rrf_score", 0.0)), reverse=True)
    for r in out:
        r.pop("_rrf_id", None)
    return out


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    id_key: str,
    k: int = 60,
) -> list[dict[str, Any]]:
    """
    Standard RRF over multiple ranked lists of dicts that share a stable `id_key`.

    score(d) = sum_i 1 / (k + rank_i(d)) for lists where d appears.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for lst in ranked_lists:
        for rank, row in enumerate(lst, start=1):
            rid = str(row[id_key])
            if not rid:
                continue
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
            if rid not in by_id:
                by_id[rid] = dict(row)
    for rid, s in scores.items():
        if rid in by_id:
            by_id[rid]["_rrf_score"] = s
    merged = sorted(
        by_id.values(),
        key=lambda r: float(r.get("_rrf_score", 0.0)),
        reverse=True,
    )
    return merged


def fuse_vector_and_graph(
    vector_rows: list[dict[str, Any]],
    graph_rows: list[dict[str, Any]],
    *,
    k: int = 60,
) -> list[dict[str, Any]]:
    """RRF with disjoint id spaces: vector rows keyed by filename, graph by fqn.

    Overlapping results for the same file path (vector chunk + graph neighborhood)
    are merged with ``_dedupe_merged_by_filename`` so each path appears once.
    """
    v_norm: list[dict[str, Any]] = []
    for r in vector_rows:
        fn = str(r.get("filename", ""))
        v_norm.append({**r, "_source": "vector", "_rrf_id": f"v:{fn}"})
    g_norm: list[dict[str, Any]] = []
    for r in graph_rows:
        gid = str(r.get("fqn") or r.get("file_key") or r.get("context_id", ""))
        g_norm.append({**r, "_source": "graph", "_rrf_id": f"g:{gid}"})
    merged = reciprocal_rank_fusion([v_norm, g_norm], id_key="_rrf_id", k=k)
    return _dedupe_merged_by_filename(merged)
