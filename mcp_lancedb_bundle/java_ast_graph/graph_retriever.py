"""Query-time: Kuzu Cypher helpers for graph expansion and structural lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import kuzu

from java_ast_graph.kuzu_io import default_db_path, open_connection

_IDENTIFIER = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")

_QUERY_TOKEN_STOP = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "have",
        "when",
        "what",
        "where",
        "your",
        "will",
        "into",
        "just",
        "like",
        "some",
        "than",
        "then",
        "them",
        "very",
        "also",
        "back",
        "here",
        "over",
        "only",
        "about",
        "after",
        "before",
        "could",
        "other",
        "which",
        "their",
        "there",
        "these",
        "those",
        "being",
        "each",
        "both",
        "does",
        "done",
        "most",
        "make",
        "many",
        "even",
        "well",
        "must",
        "same",
        "used",
        "using",
        "need",
        "work",
        "code",
        "java",
        "file",
        "class",
        "method",
        "type",
        "get",
        "add",
        "new",
        "and",
        "are",
        "but",
        "for",
        "not",
        "all",
        "can",
        "has",
        "was",
        "the",
        "any",
        "how",
        "its",
        "off",
        "our",
        "out",
        "per",
        "run",
        "way",
        "who",
        "use",
        "may",
        "one",
        "see",
        "end",
    }
)

_EXCLUDE_ID = {
    "String",
    "List",
    "Set",
    "Map",
    "Object",
    "Class",
    "Long",
    "Integer",
    "Boolean",
    "Double",
    "Float",
    "Void",
    "Exception",
    "Error",
    "Override",
    "Suppression",
    "Test",
    "NotNull",
    "Nullable",
}


@dataclass
class TypeRow:
    fqn: str
    kind: str
    file_key: str


# Must match `extract._type_kind_for_decl` (interface_declaration → "interface").
KIND_INTERFACE = "interface"


def _db_path_exists(path: Path) -> bool:
    return path.exists()


def get_readonly_graph() -> tuple[kuzu.Connection, Path] | None:
    p = default_db_path()
    if not _db_path_exists(p):
        return None
    _db, conn = open_connection(p)
    return conn, p


def find_types_in_file_by_rel_path(
    conn: kuzu.Connection,
    rel_path: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Type FQNs declared in a file (matches ``File.rel_path`` to the LanceDB chunk ``filename``)."""
    p = (rel_path or "").strip()
    if not p:
        return []
    r = conn.execute(
        "MATCH (t:Type)-[:F_DECLARED_IN]->(f:File) "
        "WHERE f.rel_path = $p RETURN t.fqn LIMIT $lim",
        {"p": p, "lim": int(limit)},
    )
    return [str(row[0]) for row in r.get_all()]


def find_types_by_name_substring(
    conn: kuzu.Connection,
    needle: str,
    *,
    limit: int = 20,
) -> list[TypeRow]:
    # Kuzu `contains(haystack, needle)` is a literal substring match (not SQL LIKE).
    n = needle.lower()
    r = conn.execute(
        "MATCH (t:Type) WHERE contains(lower(t.fqn), $n) OR contains(lower(t.simple_name), $n) "
        "RETURN t.fqn, t.kind, t.file_key ORDER BY size(t.fqn) LIMIT $lim",
        {"n": n, "lim": limit},
    )
    return [
        TypeRow(fqn=row[0], kind=row[1], file_key=row[2]) for row in r.get_all()
    ]


def list_implementors(
    conn: kuzu.Connection,
    interface_fqn: str,
    *,
    limit: int = 100,
) -> list[str]:
    r = conn.execute(
        "MATCH (c:Type)-[:T_IMPLEMENTS]->(i:Type {fqn: $if}) "
        "RETURN c.fqn LIMIT $lim",
        {"if": interface_fqn, "lim": limit},
    )
    return [row[0] for row in r.get_all()]


def list_injectors_of(
    conn: kuzu.Connection,
    target_fqn: str,
    *,
    limit: int = 100,
) -> list[str]:
    r = conn.execute(
        "MATCH (a:Type)-[:T_INJECTS]->(b:Type {fqn: $t}) "
        "RETURN a.fqn LIMIT $lim",
        {"t": target_fqn, "lim": limit},
    )
    return [row[0] for row in r.get_all()]


def type_kind_file_by_fqns(
    conn: kuzu.Connection,
    fqns: list[str],
) -> dict[str, tuple[str, str]]:
    """Map FQN -> (kind, file_key) for known Type nodes; unknown FQNs omitted."""
    u = [x for x in dict.fromkeys(fqns) if x]
    if not u:
        return {}
    r = conn.execute(
        "MATCH (t:Type) WHERE t.fqn IN $f RETURN t.fqn, t.kind, t.file_key",
        {"f": u},
    )
    return {
        str(row[0]): (str(row[1]), str(row[2])) for row in r.get_all()
    }


_STRUCTURAL_NEIGHBOR_QUERIES: list[tuple[str, str]] = [
    ("MATCH (a:Type {fqn: $s})-[:T_EXTENDS]->(b:Type) RETURN b.fqn, b.kind, b.file_key", "extends"),
    ("MATCH (a:Type {fqn: $s})-[:T_IMPLEMENTS]->(b:Type) RETURN b.fqn, b.kind, b.file_key", "implements"),
    ("MATCH (a:Type {fqn: $s})-[:T_INJECTS]->(b:Type) RETURN b.fqn, b.kind, b.file_key", "injects"),
    ("MATCH (a:Type {fqn: $s})<-[:T_EXTENDS]-(b:Type) RETURN b.fqn, b.kind, b.file_key", "rev_extends"),
    ("MATCH (a:Type {fqn: $s})<-[:T_IMPLEMENTS]-(b:Type) RETURN b.fqn, b.kind, b.file_key", "rev_implements"),
    ("MATCH (a:Type {fqn: $s})<-[:T_INJECTS]-(b:Type) RETURN b.fqn, b.kind, b.file_key", "rev_injects"),
]


def expand_neighbors_bidirectional(
    conn: kuzu.Connection,
    seed_fqns: list[str],
    *,
    depth: int = 1,
    limit: int = 200,
) -> list[dict[str, object]]:
    """Bidirectional BFS to depth `depth` over T_EXTENDS, T_IMPLEMENTS, T_INJECTS (both directions).

    Within each hop, relationship kinds are applied across the whole frontier before the next
    kind (then all frontier nodes) so 1-hop coverage is more balanced than seed-major order.
    """
    if not seed_fqns or depth < 1:
        return []
    current: list[str] = [s for s in dict.fromkeys(seed_fqns) if s]
    if not current:
        return []
    seen: set[str] = set(current)
    out_rows: list[dict[str, object]] = []
    for _hop in range(int(depth)):
        if not current or len(out_rows) >= int(limit):
            break
        nxt: list[str] = []
        for q, tag in _STRUCTURAL_NEIGHBOR_QUERIES:
            for s in current:
                if len(out_rows) >= int(limit):
                    return out_rows
                res = conn.execute(q, {"s": s})
                for row in res.get_all():
                    fqn = str(row[0])
                    if fqn in seen:
                        continue
                    seen.add(fqn)
                    nxt.append(fqn)
                    out_rows.append(
                        {
                            "fqn": fqn,
                            "kind": row[1],
                            "file_key": row[2],
                            "edge": tag,
                            "from": s,
                        }
                    )
                    if len(out_rows) >= int(limit):
                        return out_rows
        current = list(dict.fromkeys(nxt))
    return out_rows


def expand_interface_consumers(
    conn: kuzu.Connection,
    candidate_fqns: list[str],
    *,
    limit: int = 200,
) -> list[dict[str, object]]:
    """For each `interface` in ``candidate_fqns``, add implementors and `T_INJECTS` sources (DKB step 5)."""
    u = [x for x in dict.fromkeys(candidate_fqns) if x]
    if not u or limit < 1:
        return []
    met = type_kind_file_by_fqns(conn, u)
    interfaces: list[str] = [f for f, (k, _) in met.items() if k == KIND_INTERFACE]
    if not interfaces:
        return []
    out_rows: list[dict[str, object]] = []
    out_fqn: set[str] = set()
    per_iface_cap = max(8, int(limit) // max(1, len(interfaces)))
    for ifn in interfaces:
        if len(out_rows) >= int(limit):
            break
        for impl in list_implementors(conn, ifn, limit=per_iface_cap):
            if len(out_rows) >= int(limit):
                break
            fqn = str(impl)
            if fqn in out_fqn:
                continue
            kf = met.get(fqn) or type_kind_file_by_fqns(conn, [fqn]).get(fqn)
            if not kf:
                continue
            out_fqn.add(fqn)
            out_rows.append(
                {
                    "fqn": fqn,
                    "kind": kf[0],
                    "file_key": kf[1],
                    "edge": "iface_impl",
                    "from": ifn,
                }
            )
        for inj in list_injectors_of(conn, ifn, limit=per_iface_cap):
            if len(out_rows) >= int(limit):
                break
            fqn = str(inj)
            if fqn in out_fqn:
                continue
            kf = met.get(fqn) or type_kind_file_by_fqns(conn, [fqn]).get(fqn)
            if not kf:
                continue
            out_fqn.add(fqn)
            out_rows.append(
                {
                    "fqn": fqn,
                    "kind": kf[0],
                    "file_key": kf[1],
                    "edge": "iface_injectors",
                    "from": ifn,
                }
            )
    return out_rows


def collect_graph_seeds(
    query: str,
    vector_rows: list[dict[str, object]],
    conn: kuzu.Connection,
    *,
    include_chunk_seeds: bool = True,
    max_chunk_text_per_hit: int = 8000,
    max_vector_files: int = 10,
    file_types_limit: int = 8,
    name_substring_limit: int = 8,
    id_seed_top_k_query: int = 6,
    id_seed_top_k_combined: int = 20,
    substring_top_k_query: int = 8,
    substring_top_k_combined: int = 16,
    max_seeds: int = 32,
) -> list[str]:
    """Resolve DKB V0: types from top-k file paths, query + optional chunk text (steps 2–3)."""
    out: list[str] = []
    for fn in list(
        dict.fromkeys(
            [str(r.get("filename", "")) for r in vector_rows if r.get("filename")]
        )
    )[: int(max_vector_files)]:
        out.extend(
            find_types_in_file_by_rel_path(conn, fn, limit=int(file_types_limit))
        )
    for tok in guess_identifier_seeds(query, top_k=int(id_seed_top_k_query)):
        out.extend(
            [h.fqn for h in find_types_by_name_substring(conn, tok, limit=int(name_substring_limit))]
        )
    for tok in guess_substring_seeds_from_query(
        query, min_len=4, top_k=int(substring_top_k_query)
    ):
        out.extend(
            [h.fqn for h in find_types_by_name_substring(conn, tok, limit=int(name_substring_limit))]
        )
    if include_chunk_seeds:
        parts: list[str] = [query]
        for r in vector_rows:
            t = r.get("text")
            if t:
                parts.append(str(t)[: int(max_chunk_text_per_hit)])
        combined = "\n".join(parts)
        for tok in guess_identifier_seeds(combined, top_k=int(id_seed_top_k_combined)):
            out.extend(
                [h.fqn for h in find_types_by_name_substring(conn, tok, limit=int(name_substring_limit))]
            )
        for tok in guess_substring_seeds_from_query(
            combined, min_len=4, top_k=int(substring_top_k_combined)
        ):
            out.extend(
                [h.fqn for h in find_types_by_name_substring(conn, tok, limit=int(name_substring_limit))]
            )
    return [x for x in dict.fromkeys(out) if x][: int(max_seeds)]


def guess_substring_seeds_from_query(
    text: str,
    *,
    min_len: int = 4,
    top_k: int = 8,
) -> list[str]:
    """Alphanumeric-ish tokens (substring seeds) for ``find_types_by_name_substring``."""
    parts = re.split(r"[^A-Za-z0-9]+", text)
    out: list[str] = []
    for w in parts:
        w = w.strip()
        if len(w) < int(min_len) or w.lower() in _QUERY_TOKEN_STOP:
            continue
        out.append(w)
    return list(dict.fromkeys(out))[: int(top_k)]


def guess_identifier_seeds(text: str, top_k: int = 5) -> list[str]:
    """Heuristic Java-type-like tokens for graph seeding."""
    cands: list[str] = []
    for m in _IDENTIFIER.finditer(text):
        w = m.group(1)
        if w in _EXCLUDE_ID:
            continue
        cands.append(w)
    return list(dict.fromkeys(cands))[:top_k]


def read_file_snippet(
    project_root: Path,
    rel_path: str,
    *,
    max_bytes: int = 6000,
) -> str:
    """Best-effort read of file under project (or parent) trees."""
    for parent in [project_root, *list(project_root.parents)[:5]]:
        cand = (parent / rel_path).resolve()
        if cand.is_file() and str(cand).startswith(str(parent)):
            try:
                return cand.read_text(encoding="utf-8", errors="replace")[:max_bytes]
            except OSError:
                continue
    return ""
