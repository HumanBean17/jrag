"""Helpers that bridge `ast_java` output with chunk- and graph-level metadata.

Used both by the CocoIndex indexer (for per-chunk enrichment) and by
`build_ast_graph.py` (for module / microservice inference and deterministic
node ids).

Two location concepts are tracked per file:

- **module** — the *innermost* build-marker ancestor (Maven / Gradle /
  SBT). Same as the legacy `service` field. Useful for module-scoped
  search inside a microservice.
- **microservice** — the *outermost* build-marker ancestor under
  `project_root`. Represents one deployable / repo. Resolution order:
    1. explicit override list (YAML at project root);
    2. outermost build marker between `project_root` and the file;
    3. first path segment under `project_root`;
    4. empty.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from ast_java import (
    AnnotationRef,
    JavaFileAst,
    MethodDecl,
    OutgoingCallDecl,
    RouteDecl,
    ROUTE_META_ANNOTATION_NAMES,
    TypeDecl,
    _ROUTE_HTTP_MAPPING_NAMES,
    CODEBASE_HTTP_CLIENT_ANNOTATIONS,
    CODEBASE_PRODUCER_ANNOTATIONS,
    infer_capabilities_for_type,
    infer_role_for_type,
    parse_java,
    ROLE_ANNOTATIONS,
    _METHOD_ANN_TO_CAPABILITY,
    _TYPE_ANN_TO_CAPABILITY,
)
from java_ontology import (
    VALID_CAPABILITIES,
    VALID_CLIENT_KINDS,
    VALID_PRODUCER_KINDS,
    VALID_ROLES,
    VALID_ROUTE_FRAMEWORKS,
    VALID_ROUTE_KINDS,
)
from path_filtering import LayeredIgnore, iter_java_source_files

__all__ = [
    "AnnotationDecl",
    "BrownfieldOverrides",
    "ChunkEnrichment",
    "annotation_meta_decls_from_graph_tables",
    "collect_annotation_meta_chain",
    "compute_meta_chains_from_decls",
    "enrich_chunk",
    "load_brownfield_overrides",
    "load_microservice_overrides",
    "module_for_path",
    "microservice_for_path",
    "resolve_role_and_capabilities",
    "resolve_routes_for_method",
    "resolve_http_client_for_method",
    "resolve_async_producer_for_method",
    "RouteHint",
    "HttpClientHint",
    "AsyncProducerHint",
    "symbol_id",
    "phantom_id",
    "BUILD_MARKERS",
    "CONFIG_FILENAMES",
]

BUILD_MARKERS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
)

# Recognised config filenames at `project_root` (first match wins).
CONFIG_FILENAMES = (".java-codebase-rag.yml", ".java-codebase-rag.yaml")


@dataclass
class ChunkEnrichment:
    package: str
    module: str
    microservice: str
    primary_type_fqn: str
    primary_type_kind: str
    role: str
    annotations_on_type: list[str]
    symbols: list[str]
    capabilities: list[str] = field(default_factory=list)


# ---------- microservice override loading ----------


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


@lru_cache(maxsize=64)
def _load_config_microservice_roots(project_root_str: str) -> tuple[str, ...]:
    """Read `microservice_roots` from `.java-codebase-rag.yml` at project_root.

    Cached per project_root to avoid re-reading on every chunk. Failures
    (file missing, malformed YAML, missing key) silently return an empty
    tuple — config is strictly opt-in.
    """
    root = Path(project_root_str)
    for name in CONFIG_FILENAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            import yaml  # PyYAML; already a transitive dep of cocoindex
        except ImportError:
            return ()
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            return ()
        if not isinstance(data, dict):
            return ()
        raw = data.get("microservice_roots")
        if isinstance(raw, str):
            return tuple(_parse_csv(raw))
        if isinstance(raw, list):
            return tuple(str(x).strip() for x in raw if str(x).strip())
        return ()
    return ()


@lru_cache(maxsize=64)
def _load_config_cross_service_resolution(project_root_str: str) -> str:
    """Read `cross_service_resolution` from `.java-codebase-rag.yml` at project_root.

    Returns "auto" or "brownfield_only". Defaults to "auto" when the key is absent
    or the file is missing / malformed. Unknown values warn on stderr and fall back
    to "auto".
    """
    root = Path(project_root_str)
    for name in CONFIG_FILENAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            import yaml  # PyYAML; already a transitive dep of cocoindex
        except ImportError:
            return "auto"
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            return "auto"
        if not isinstance(data, dict):
            return "auto"
        val = data.get("cross_service_resolution", "auto")
        if val not in {"auto", "brownfield_only"}:
            print(
                f"[lancedb-mcp] cross_service_resolution: unknown value "
                f"{val!r}, falling back to 'auto'",
                file=sys.stderr,
            )
            return "auto"
        return val
    return "auto"


def load_microservice_overrides(project_root: str | Path | None) -> tuple[str, ...]:
    """Microservice root overrides from project YAML only (`microservice_roots:`)."""
    out: list[str] = []
    seen: set[str] = set()

    if project_root is not None:
        try:
            root_str = str(Path(project_root).resolve())
        except OSError:
            root_str = str(project_root)
        for name in _load_config_microservice_roots(root_str):
            if name not in seen:
                seen.add(name)
                out.append(name)

    return tuple(out)


# ---------- brownfield role / capability overrides ----------


@dataclass(frozen=True)
class RouteHint:
    """YAML `route_overrides` entry: maps to `RouteDecl` fields (B2a brownfield)."""

    framework: str
    kind: str
    path: str = ""
    method: str = ""
    topic: str = ""
    broker: str = ""


@dataclass(frozen=True)
class HttpClientHint:
    client_kind: str
    target_service: str = ""
    path: str = ""
    method: str = ""


@dataclass(frozen=True)
class AsyncProducerHint:
    client_kind: str
    topic: str = ""
    broker: str = ""


@dataclass(frozen=True)
class BrownfieldOverrides:
    annotation_to_role: dict[str, str] = field(default_factory=dict)
    annotation_to_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    fqn_role: dict[str, str] = field(default_factory=dict)
    fqn_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    annotation_to_route_hint: dict[str, RouteHint] = field(default_factory=dict)
    fqn_to_route_hint: dict[str, RouteHint] = field(default_factory=dict)
    annotation_to_http_client_hint: dict[str, HttpClientHint] = field(default_factory=dict)
    fqn_to_http_client_hint: dict[str, HttpClientHint] = field(default_factory=dict)
    annotation_to_async_producer_hint: dict[str, AsyncProducerHint] = field(default_factory=dict)
    fqn_to_async_producer_hint: dict[str, AsyncProducerHint] = field(default_factory=dict)


def _meta_builtins() -> frozenset[str]:
    return (
        frozenset(ROLE_ANNOTATIONS)
        | frozenset(_METHOD_ANN_TO_CAPABILITY)
        | frozenset(_TYPE_ANN_TO_CAPABILITY)
        | ROUTE_META_ANNOTATION_NAMES
        | CODEBASE_HTTP_CLIENT_ANNOTATIONS
        | CODEBASE_PRODUCER_ANNOTATIONS
    )


# Rounds in the iterative closure; `max_depth` of 4 = at most four hops
# from any annotation to a built-in in the plan's `_build_meta_chain` sketch
# (e.g. six linear wrappers to `@Service` leaves the outer name without a role
# from Layer A).
_META_PATH_DEPTH_CAP = 4


@dataclass(frozen=True)
class AnnotationDecl:
    fqn: str
    simple: str
    meta_annotations: tuple[str, ...]


def _build_meta_chain(
    decls: dict[str, AnnotationDecl],
    builtins: frozenset[str],
    *,
    max_depth: int,
) -> dict[str, frozenset[str]]:
    """Iterative fixed-point over the meta-annotation graph (PLAN-BROWNFIELD, Pass A2)."""
    chain: dict[str, set[str]] = {b: {b} for b in builtins}
    for _ in range(max_depth):
        changed = False
        for _sk, decl in sorted(decls.items(), key=lambda kv: kv[0]):
            reach: set[str] = set()
            for parent in decl.meta_annotations:
                reach |= chain.get(parent, set())
            if reach and not reach.issubset(chain.get(decl.simple, set())):
                chain.setdefault(decl.simple, set()).update(reach)
                changed = True
        if not changed:
            break
    return {k: frozenset(chain.get(k, set())) for k in decls}


def _collect_annotation_decl_index(project_root_str: str) -> dict[str, AnnotationDecl]:
    """File scan for `@interface` declarations; sorted paths for stable first-wins (Fix 5/6)."""
    root = Path(project_root_str)
    if not root.is_dir():
        return {}
    ignore = LayeredIgnore(root)
    decls: dict[str, AnnotationDecl] = {}
    for p in sorted(iter_java_source_files(root, ignore=ignore), key=str):
        try:
            content = p.read_bytes()
        except OSError as exc:
            print(
                f"[lancedb-mcp] skipped unreadable {p}: {exc}",
                file=sys.stderr,
            )
            continue
        if not content.strip():
            continue
        try:
            jast = parse_java(content)
        except Exception as exc:
            print(
                f"[lancedb-mcp] parse error in {p}: {exc}",
                file=sys.stderr,
            )
            continue
        for t in jast.all_types:
            if t.kind != "annotation":
                continue
            if t.name in decls:
                print(
                    f"[lancedb-mcp] duplicate @interface simple name {t.name!r} — "
                    f"keeping {decls[t.name].fqn!r}, ignoring {t.fqn!r}",
                    file=sys.stderr,
                )
                continue
            decls[t.name] = AnnotationDecl(
                fqn=t.fqn,
                simple=t.name,
                meta_annotations=tuple(a.name for a in t.annotations),
            )
    return decls


@lru_cache(maxsize=4)
def collect_annotation_meta_chain(
    project_root_str: str,
) -> dict[str, frozenset[str]]:
    """Map annotation simple name → built-in simple names reachable via meta-annotations.

    Single source of truth for Layer A: both the LadybugDB writer and Lance chunk
    enrichment must use this; they must not derive `meta_chain` from separate
    filesystem walks. See ``PLAN-BROWNFIELD-ROLE-OVERRIDES`` §
    *Single source of truth (REQUIRED — read before implementation)*.
    """
    decls = _collect_annotation_decl_index(project_root_str)
    b = _meta_builtins()
    return _build_meta_chain(decls, b, max_depth=_META_PATH_DEPTH_CAP)


def annotation_meta_decls_from_graph_tables(
    types: dict[str, Any],
) -> dict[str, tuple[str, ...]]:
    """From `build_ast_graph.GraphTables.types`, map @interface simple name -> meta anns.

    Used for diagnostics; Layer A in production uses `collect_annotation_meta_chain`
    (disk) so LadybugDB and Lance share one index.
    """
    decls: dict[str, tuple[str, ...]] = {}
    first_fqn: dict[str, str] = {}
    for e in types.values():
        d = e.decl
        if d.kind != "annotation":
            continue
        if d.name in decls:
            print(
                f"[lancedb-mcp] duplicate @interface simple name {d.name!r} — "
                f"keeping {first_fqn[d.name]!r}, ignoring {d.fqn!r}",
                file=sys.stderr,
            )
            continue
        first_fqn[d.name] = d.fqn
        decls[d.name] = tuple(a.name for a in d.annotations)
    return decls


def compute_meta_chains_from_decls(
    decls: dict[str, tuple[str, ...]],
) -> dict[str, frozenset[str]]:
    """Map annotation simple name → transitive built-in simple names (Layer A), tests/legacy.

    Shape-only callers use placeholder FQNs; use `collect_annotation_meta_chain` for
    a stable project index.
    """
    adecls: dict[str, AnnotationDecl] = {
        s: AnnotationDecl(
            fqn=f"::{s}",
            simple=s,
            meta_annotations=meta,
        )
        for s, meta in decls.items()
    }
    b = _meta_builtins()
    return _build_meta_chain(adecls, b, max_depth=_META_PATH_DEPTH_CAP)


@lru_cache(maxsize=64)
def _load_brownfield_overrides(project_root_str: str) -> BrownfieldOverrides:
    """Read `role_overrides` from `.java-codebase-rag.yml` at project_root. Cached per root."""
    root = Path(project_root_str)
    valid_roles = VALID_ROLES
    valid_caps = VALID_CAPABILITIES
    for name in CONFIG_FILENAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            import yaml  # PyYAML; already a transitive dep of cocoindex
        except ImportError:
            return BrownfieldOverrides({}, {}, {}, {}, {}, {}, {}, {}, {}, {})
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            return BrownfieldOverrides({}, {}, {}, {}, {}, {}, {}, {}, {}, {})
        if not isinstance(data, dict):
            return BrownfieldOverrides({}, {}, {}, {}, {}, {}, {}, {}, {}, {})
        ro = data.get("role_overrides")
        if not isinstance(ro, dict):
            ro = {}
        a_to_r: dict[str, str] = {}
        a_to_c: dict[str, tuple[str, ...]] = {}
        fqn_r: dict[str, str] = {}
        fqn_c: dict[str, tuple[str, ...]] = {}

        ann = ro.get("annotations")
        if isinstance(ann, dict):
            for k, v in ann.items():
                ks = str(k).strip()
                if not ks:
                    continue
                vs = str(v).strip()
                if not vs:
                    continue
                if vs not in valid_roles:
                    print(
                        f"[lancedb-mcp] role_overrides.annotations: unknown role {vs!r} for {ks!r} — dropped",
                        file=sys.stderr,
                    )
                    continue
                a_to_r[ks] = vs

        caps_block = ro.get("capabilities")
        if isinstance(caps_block, dict):
            for k, v in caps_block.items():
                ks = str(k).strip()
                if not ks or not isinstance(v, (list, tuple)):
                    continue
                out_cp: list[str] = []
                for item in v:
                    cap = str(item).strip()
                    if not cap:
                        continue
                    if cap not in valid_caps:
                        print(
                            f"[lancedb-mcp] role_overrides.capabilities: unknown capability {cap!r} for {ks!r} — dropped",
                            file=sys.stderr,
                        )
                        continue
                    out_cp.append(cap)
                if out_cp:
                    a_to_c[ks] = tuple(out_cp)

        fqn = ro.get("fqn")
        if isinstance(fqn, dict):
            for fqn_key, v in fqn.items():
                fk = str(fqn_key).strip()
                if not fk or not isinstance(v, dict):
                    continue
                r = v.get("role")
                if r is not None and str(r).strip():
                    rs = str(r).strip()
                    if rs in valid_roles:
                        fqn_r[fk] = rs
                    else:
                        print(
                            f"[lancedb-mcp] role_overrides.fqn: unknown role {rs!r} for {fk!r} — dropped",
                            file=sys.stderr,
                        )
                cap_list = v.get("capabilities")
                if isinstance(cap_list, (list, tuple)):
                    out_c: list[str] = []
                    for item in cap_list:
                        cap = str(item).strip()
                        if not cap:
                            continue
                        if cap not in valid_caps:
                            print(
                                f"[lancedb-mcp] role_overrides.fqn: unknown capability {cap!r} for {fk!r} — dropped",
                                file=sys.stderr,
                            )
                            continue
                        out_c.append(cap)
                    if out_c:
                        fqn_c[fk] = tuple(out_c)

        a_route: dict[str, RouteHint] = {}
        f_route: dict[str, RouteHint] = {}
        a_http: dict[str, HttpClientHint] = {}
        f_http: dict[str, HttpClientHint] = {}
        a_async: dict[str, AsyncProducerHint] = {}
        f_async: dict[str, AsyncProducerHint] = {}
        r_ov = data.get("route_overrides")
        if isinstance(r_ov, dict):
            ann_rt = r_ov.get("annotations")
            if isinstance(ann_rt, dict):
                for key, val in ann_rt.items():
                    ks = str(key).strip()
                    if not ks or not isinstance(val, dict):
                        continue
                    fw = str(val.get("framework", "") or "").strip()
                    kd = str(val.get("kind", "") or "").strip()
                    if fw not in VALID_ROUTE_FRAMEWORKS:
                        print(
                            f"[lancedb-mcp] route_overrides.annotations: unknown framework {fw!r} "
                            f"for key {ks!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    if kd not in VALID_ROUTE_KINDS:
                        print(
                            f"[lancedb-mcp] route_overrides.annotations: unknown kind {kd!r} "
                            f"for key {ks!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    a_route[ks] = RouteHint(
                        framework=fw,
                        kind=kd,
                        path=str(val.get("path", "") or "").strip(),
                        method=str(val.get("method", "") or "").strip().upper(),
                        topic=str(val.get("topic", "") or "").strip(),
                        broker=str(val.get("broker", "") or "").strip(),
                    )
            fqn_rt = r_ov.get("fqn")
            if isinstance(fqn_rt, dict):
                for fqn_key, val in fqn_rt.items():
                    fk = str(fqn_key).strip()
                    if not fk or not isinstance(val, dict):
                        continue
                    fw = str(val.get("framework", "") or "").strip()
                    kd = str(val.get("kind", "") or "").strip()
                    if fw not in VALID_ROUTE_FRAMEWORKS:
                        print(
                            f"[lancedb-mcp] route_overrides.fqn: unknown framework {fw!r} "
                            f"for key {fk!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    if kd not in VALID_ROUTE_KINDS:
                        print(
                            f"[lancedb-mcp] route_overrides.fqn: unknown kind {kd!r} "
                            f"for key {fk!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    f_route[fk] = RouteHint(
                        framework=fw,
                        kind=kd,
                        path=str(val.get("path", "") or "").strip(),
                        method=str(val.get("method", "") or "").strip().upper(),
                        topic=str(val.get("topic", "") or "").strip(),
                        broker=str(val.get("broker", "") or "").strip(),
                    )

        http_ov = data.get("http_client_overrides")
        if isinstance(http_ov, dict):
            ann_http = http_ov.get("annotations")
            if isinstance(ann_http, dict):
                for key, val in ann_http.items():
                    ks = str(key).strip()
                    if not ks or not isinstance(val, dict):
                        continue
                    ck = str(val.get("client_kind", "") or "").strip()
                    if ck not in VALID_CLIENT_KINDS:
                        print(
                            f"[lancedb-mcp] http_client_overrides.annotations: unknown client_kind {ck!r} "
                            f"for key {ks!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    a_http[ks] = HttpClientHint(
                        client_kind=ck,
                        target_service=str(val.get("target_service", "") or "").strip(),
                        path=str(val.get("path", "") or "").strip(),
                        method=str(val.get("method", "") or "").strip().upper(),
                    )
            fqn_http = http_ov.get("fqn")
            if isinstance(fqn_http, dict):
                for fqn_key, val in fqn_http.items():
                    fk = str(fqn_key).strip()
                    if not fk or not isinstance(val, dict):
                        continue
                    ck = str(val.get("client_kind", "") or "").strip()
                    if ck not in VALID_CLIENT_KINDS:
                        print(
                            f"[lancedb-mcp] http_client_overrides.fqn: unknown client_kind {ck!r} "
                            f"for key {fk!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    f_http[fk] = HttpClientHint(
                        client_kind=ck,
                        target_service=str(val.get("target_service", "") or "").strip(),
                        path=str(val.get("path", "") or "").strip(),
                        method=str(val.get("method", "") or "").strip().upper(),
                    )

        async_ov = data.get("async_producer_overrides")
        if isinstance(async_ov, dict):
            ann_async = async_ov.get("annotations")
            if isinstance(ann_async, dict):
                for key, val in ann_async.items():
                    ks = str(key).strip()
                    if not ks or not isinstance(val, dict):
                        continue
                    ck = str(val.get("client_kind", "") or "").strip()
                    if ck not in VALID_PRODUCER_KINDS:
                        print(
                            f"[lancedb-mcp] async_producer_overrides.annotations: unknown client_kind {ck!r} "
                            f"for key {ks!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    a_async[ks] = AsyncProducerHint(
                        client_kind=ck,
                        topic=str(val.get("topic", "") or "").strip(),
                        broker=str(val.get("broker", "") or "").strip(),
                    )
            fqn_async = async_ov.get("fqn")
            if isinstance(fqn_async, dict):
                for fqn_key, val in fqn_async.items():
                    fk = str(fqn_key).strip()
                    if not fk or not isinstance(val, dict):
                        continue
                    ck = str(val.get("client_kind", "") or "").strip()
                    if ck not in VALID_PRODUCER_KINDS:
                        print(
                            f"[lancedb-mcp] async_producer_overrides.fqn: unknown client_kind {ck!r} "
                            f"for key {fk!r} — entry dropped",
                            file=sys.stderr,
                        )
                        continue
                    f_async[fk] = AsyncProducerHint(
                        client_kind=ck,
                        topic=str(val.get("topic", "") or "").strip(),
                        broker=str(val.get("broker", "") or "").strip(),
                    )

        return BrownfieldOverrides(
            a_to_r,
            a_to_c,
            fqn_r,
            fqn_c,
            a_route,
            f_route,
            a_http,
            f_http,
            a_async,
            f_async,
        )
    return BrownfieldOverrides({}, {}, {}, {}, {}, {}, {}, {}, {}, {})


def load_brownfield_overrides(
    project_root: str | Path | None,
) -> BrownfieldOverrides:
    if project_root is None:
        return BrownfieldOverrides({}, {}, {}, {}, {}, {}, {}, {}, {}, {})
    try:
        r = str(Path(project_root).resolve())
    except OSError:
        r = str(project_root)
    return _load_brownfield_overrides(r)


def resolve_role_and_capabilities(
    type_decl: TypeDecl,
    *,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, frozenset[str]] | None = None,
) -> tuple[str, list[str]]:
    """Compose AST inference with brownfield overrides (single execution order).

    The resolver runs the steps **below in order**; each step mutates the same
    working ``(role, caps)``. Steps listed later in this docstring *override
    or extend* the result of earlier steps when they apply. There is no second
    "priority" axis: "last to run" in this list is the strongest.

    1. Built-in inference (``infer_role_for_type`` / ``infer_capabilities_for_type``)
    2. Layer B — config annotation map (``role_overrides.annotations`` / ``capabilities``)
    3. Layer A — meta-annotation walk (``meta_chain``; Phase 2; no-op if None)
    4. Layer C — ``@CodebaseRole`` / ``@CodebaseCapability`` in source
    5. Layer B — per-FQN map (``role_overrides.fqn``)

    Role rule: steps 2 and 3 that change *role* use ``if role == "OTHER"`` on the
    *current* role, so step 2 (user config) runs before step 3: explicit config
    wins over automatic meta (see `PLAN-BROWNFIELD-ROLE-OVERRIDES` §
    *Resolver execution order*). Steps 4 and 5 apply to role without that guard.
    Capability rule: every layer is additively unioned; return value is
    ``sorted(caps)`` for a stable on-disk form.

    See ``PLAN-BROWNFIELD-ROLE-OVERRIDES`` § *Resolver execution order* for the
    side-by-side table.
    """
    # ----- Step 1: built-in inference (runs first) -----
    role = infer_role_for_type(type_decl)
    caps: set[str] = set(infer_capabilities_for_type(type_decl))
    type_ann_names = [a.name for a in type_decl.annotations]

    # ----- Step 2: Layer B — annotation name map (before meta-walk) -----
    if role == "OTHER":
        for ann in type_ann_names:
            mapped = overrides.annotation_to_role.get(ann)
            if mapped:
                role = mapped
                break
    for ann in type_ann_names:
        for c in overrides.annotation_to_capabilities.get(ann, ()):
            caps.add(c)
    for m in type_decl.methods:
        for ann in m.annotations:
            for c in overrides.annotation_to_capabilities.get(ann.name, ()):
                caps.add(c)

    # ----- Step 3: Layer A — meta-annotation chain -----
    if meta_chain is not None:
        if role == "OTHER":
            for ann in type_ann_names:
                for builtin in meta_chain.get(ann, ()):
                    mapped = ROLE_ANNOTATIONS.get(builtin)
                    if mapped:
                        role = mapped
                        break
                if role != "OTHER":
                    break
        for ann in type_ann_names:
            for builtin in meta_chain.get(ann, ()):
                c = _TYPE_ANN_TO_CAPABILITY.get(builtin)
                if c:
                    caps.add(c)
        for m in type_decl.methods:
            for ann in m.annotations:
                for builtin in meta_chain.get(ann.name, ()):
                    c = _METHOD_ANN_TO_CAPABILITY.get(builtin)
                    if c:
                        caps.add(c)

    # ----- Step 4: Layer C — in-source @CodebaseRole / @CodebaseCapability -----
    for ann in type_decl.annotations:
        if ann.name == "CodebaseRole":
            v = ann.arguments.get("value")
            vk = ann.argument_kinds.get("value")
            if vk == "string" and v is not None:
                print(
                    f"[lancedb-mcp] CodebaseRole: string literal value {v!r} is no longer supported; "
                    "use CodebaseRoleKind.*",
                    file=sys.stderr,
                )
            elif vk == "enum" and v in VALID_ROLES:
                role = v
            elif vk == "enum" and v is not None and v not in VALID_ROLES:
                print(
                    f"[lancedb-mcp] CodebaseRole: invalid value {v!r} — ignored",
                    file=sys.stderr,
                )
        elif ann.name == "CodebaseCapability":
            v = ann.arguments.get("value")
            vk = ann.argument_kinds.get("value")
            if vk == "string" and v is not None:
                print(
                    f"[lancedb-mcp] CodebaseCapability: string literal value {v!r} is no longer supported; "
                    "use CodebaseCapabilityKind.*",
                    file=sys.stderr,
                )
            elif vk == "enum" and v in VALID_CAPABILITIES:
                caps.add(v)
            elif vk == "enum" and v is not None and v not in VALID_CAPABILITIES:
                print(
                    f"[lancedb-mcp] CodebaseCapability: invalid value {v!r} — ignored",
                    file=sys.stderr,
                )
        elif ann.name == "CodebaseCapabilities":
            for v, vk in zip(
                ann.container_capability_values,
                ann.container_capability_kinds,
                strict=True,
            ):
                if vk == "string" and v:
                    print(
                        f"[lancedb-mcp] CodebaseCapabilities: string literal value {v!r} is no longer supported; "
                        "use CodebaseCapabilityKind.*",
                        file=sys.stderr,
                    )
                elif vk == "enum" and v in VALID_CAPABILITIES:
                    caps.add(v)
                elif vk == "enum" and v:
                    print(
                        f"[lancedb-mcp] CodebaseCapabilities: invalid value {v!r} — ignored",
                        file=sys.stderr,
                    )

    # ----- Step 5: Layer B — per-FQN (runs last; can override role / add caps) -----
    if type_decl.fqn in overrides.fqn_role:
        role = overrides.fqn_role[type_decl.fqn]
    for c in overrides.fqn_capabilities.get(type_decl.fqn, ()):
        caps.add(c)

    return role, sorted(caps)


_HTTP_ROUTE_KINDS = frozenset({"http_endpoint", "http_consumer"})
# Layer C `@CodebaseAsyncRoute` replaces same-method auto messaging of these kinds.
_LAYER_C_ASYNC_REPLACES_BUILTIN_KINDS = frozenset({"kafka_topic"})


def _route_path_atom(raw_value: str, value_kind: str | None) -> tuple[str, str, float, bool]:
    # Canonical ladder for route path hints: annotation -> spel -> constant_ref.
    # Note: an empty string literal is still an explicit annotation value (`annotation`).
    # "No value present" is handled by caller fallback, not by a separate value_kind.
    if value_kind == "string":
        if "${" in raw_value:
            return "", "spel", 0.85, False
        return raw_value, "annotation", 1.0, True
    return "", "constant_ref", 0.7, False


def _route_hint_lookup(ann: AnnotationRef, hints: dict[str, RouteHint]) -> RouteHint | None:
    q = ann.qualified.strip()
    if q in hints:
        return hints[q]
    if ann.name in hints:
        return hints[ann.name]
    for k, h in sorted(hints.items(), key=lambda kv: kv[0]):
        if k.endswith("." + ann.name):
            return h
    return None


def _route_decl_from_route_hint(
    hint: RouteHint,
    *,
    method_fqn: str,
    method_sig: str,
    filename: str,
    start_line: int,
    end_line: int,
    source_layer: str,
) -> RouteDecl:
    return RouteDecl(
        method_fqn=method_fqn,
        method_sig=method_sig,
        kind=hint.kind,
        framework=hint.framework,
        http_method=hint.method,
        path=hint.path,
        topic=hint.topic,
        broker=hint.broker,
        feign_name="",
        feign_url="",
        resolution_strategy="annotation",
        confidence=1.0,
        resolved=True,
        filename=filename,
        start_line=start_line,
        end_line=end_line,
        route_source_layer=source_layer,
    )


def _http_paths_from_ann_ref(ann: AnnotationRef) -> list[tuple[str, str, float, bool]]:
    """Path atoms for a custom mapping annotation (AnnotationRef only; Layer A)."""
    out: list[tuple[str, str, float, bool]] = []
    for key in ("path", "value"):
        if key not in ann.arguments:
            continue
        v = ann.arguments[key]
        vk = ann.argument_kinds.get(key)
        if not v:
            continue
        out.append(_route_path_atom(v, vk))
    if not out:
        out.append(_route_path_atom("", "string"))
    return out


def _http_methods_for_ann_ref(ann: AnnotationRef, template: str) -> list[str]:
    if template == "GetMapping":
        return ["GET"]
    if template == "PostMapping":
        return ["POST"]
    if template == "PutMapping":
        return ["PUT"]
    if template == "DeleteMapping":
        return ["DELETE"]
    if template == "PatchMapping":
        return ["PATCH"]
    if template == "RequestMapping":
        raw = ann.arguments.get("method")
        mk = ann.argument_kinds.get("method")
        if raw and mk == "enum":
            return [raw.rsplit(".", 1)[-1].upper()]
        return [""]
    return [""]


def _layer_a_route_decls_from_ann(
    ann: AnnotationRef,
    meta_chain: dict[str, frozenset[str]],
    *,
    method_fqn: str,
    method_sig: str,
    filename: str,
    start_line: int,
    end_line: int,
) -> list[RouteDecl]:
    """Synthetic HTTP routes from custom annotations whose meta-chain hits Spring mappings."""
    if ann.name in _ROUTE_HTTP_MAPPING_NAMES:
        return []
    chain = meta_chain.get(ann.name, frozenset())
    http_hits = sorted(chain & _ROUTE_HTTP_MAPPING_NAMES)
    if not http_hits:
        return []
    template = http_hits[0]
    path_atoms = _http_paths_from_ann_ref(ann)
    methods = _http_methods_for_ann_ref(ann, template)
    out: list[RouteDecl] = []
    for raw_path, strat, conf, res in path_atoms:
        for hm in methods:
            out.append(
                RouteDecl(
                    method_fqn=method_fqn,
                    method_sig=method_sig,
                    kind="http_endpoint",
                    framework="spring_mvc",
                    http_method=hm,
                    path=raw_path,
                    topic="",
                    broker="",
                    feign_name="",
                    feign_url="",
                    resolution_strategy=strat,
                    confidence=conf,
                    resolved=res,
                    filename=filename,
                    start_line=start_line,
                    end_line=end_line,
                    route_source_layer="layer_a_meta",
                ),
            )
    return out


def _merge_layer_c_codebase_routes(
    working: list[RouteDecl],
    layer_c: list[RouteDecl],
) -> list[RouteDecl]:
    """Layer C — brownfield in-source routes win over same-method auto extraction.

    HTTP: any `@CodebaseHttpRoute` for a method drops same-method **built-in** HTTP
    rows (typically `@GetMapping`), then layer C HTTP rows are appended so the
    brownfield path/method is authoritative (no field merge onto surviving built-ins).
    Async: any `@CodebaseAsyncRoute` (`kafka_topic`) for a method drops same-method
    **built-in** `kafka_topic` rows (typically `@KafkaListener`), then layer C rows
    are merged/appended so the brownfield topic is authoritative over auto extraction.
    """
    if not layer_c:
        return working
    merged = [replace(r) for r in working]
    async_override_mf = {
        cr.method_fqn
        for cr in layer_c
        if cr.kind in _LAYER_C_ASYNC_REPLACES_BUILTIN_KINDS
    }
    if async_override_mf:
        merged = [
            r
            for r in merged
            if not (
                r.method_fqn in async_override_mf
                and r.kind in _LAYER_C_ASYNC_REPLACES_BUILTIN_KINDS
                and r.route_source_layer == "builtin"
            )
        ]
    http_override_mf = {
        cr.method_fqn for cr in layer_c if cr.kind in _HTTP_ROUTE_KINDS
    }
    if http_override_mf:
        merged = [
            r
            for r in merged
            if not (
                r.method_fqn in http_override_mf
                and r.kind in _HTTP_ROUTE_KINDS
                and r.route_source_layer == "builtin"
            )
        ]
    for cr in sorted(layer_c, key=lambda x: (x.path, x.http_method, x.topic)):
        if cr.kind in _HTTP_ROUTE_KINDS:
            merged.append(replace(cr))
            continue
        placed = False
        for i, r in enumerate(merged):
            if (
                r.kind in _HTTP_ROUTE_KINDS
                and cr.kind in _HTTP_ROUTE_KINDS
                and r.method_fqn == cr.method_fqn
            ):
                merged[i] = replace(
                    r,
                    path=cr.path if cr.path else r.path,
                    http_method=cr.http_method if cr.http_method else r.http_method,
                    framework=cr.framework if cr.framework else r.framework,
                    kind=cr.kind if cr.kind else r.kind,
                    topic=cr.topic if cr.topic else r.topic,
                    broker=cr.broker if cr.broker else r.broker,
                    resolution_strategy="codebase_route",
                    confidence=cr.confidence,
                    resolved=cr.resolved,
                    route_source_layer="layer_c_source",
                )
                placed = True
                break
        if not placed:
            merged.append(replace(cr))
    return merged


def _apply_layer_b_fqn(
    working: list[RouteDecl],
    hint: RouteHint,
    *,
    method_fqn: str,
    method_sig: str,
    filename: str,
    start_line: int,
    end_line: int,
) -> list[RouteDecl]:
    """Layer B fqn — last writer; merges onto existing routes or seeds one."""
    if not working:
        return [
            _route_decl_from_route_hint(
                hint,
                method_fqn=method_fqn,
                method_sig=method_sig,
                filename=filename,
                start_line=start_line,
                end_line=end_line,
                source_layer="layer_b_fqn",
            ),
        ]
    out: list[RouteDecl] = []
    for r in working:
        out.append(
            replace(
                r,
                framework=hint.framework or r.framework,
                kind=hint.kind or r.kind,
                path=hint.path or r.path,
                http_method=hint.method or r.http_method,
                topic=hint.topic or r.topic,
                broker=hint.broker or r.broker,
                route_source_layer="layer_b_fqn",
            ),
        )
    return out


def resolve_routes_for_method(
    *,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, frozenset[str]] | None,
    builtin_routes: list[RouteDecl],
) -> list[RouteDecl]:
    """Compose built-in route extraction with brownfield overrides (single execution order).

    Mirrors ``resolve_role_and_capabilities`` layering; see ``PLAN-TIER1-COMPLETION``
    § PR-A3. Steps run **in order**; later steps override per field on the same
    route where applicable.

    1. Built-in routes from ``_collect_routes`` (excluding ``@CodebaseRoute`` stubs)
    2. Layer B — ``route_overrides.annotations`` (annotation FQN or simple name)
    3. Layer A — meta-annotation walk via ``collect_annotation_meta_chain``
    4. Layer C — in-source ``@CodebaseHttpRoute`` / ``@CodebaseAsyncRoute`` (and
       legacy ``@CodebaseRoute``) from parse; async layer C drops built-in
       ``kafka_topic`` rows for the same method before merge
    5. Layer B — ``route_overrides.fqn`` (outermost; merges onto every route)
    """
    method_fqn = f"{enclosing_type.fqn}#{method_decl.signature}"
    filename = builtin_routes[0].filename if builtin_routes else ""
    sl, el = method_decl.start_line, method_decl.end_line

    # In-source brownfield: `@CodebaseHttpRoute` marks `codebase_route`; async uses
    # the topic atom strategy but always sets `route_source_layer=layer_c_source`.
    builtins_only = [
        r
        for r in builtin_routes
        if r.route_source_layer != "layer_c_source" and r.resolution_strategy != "codebase_route"
    ]
    layer_c_src = [
        r
        for r in builtin_routes
        if r.route_source_layer == "layer_c_source" or r.resolution_strategy == "codebase_route"
    ]

    working: list[RouteDecl] = [
        replace(r, route_source_layer="builtin") for r in builtins_only
    ]

    combined_anns: list[tuple[bool, AnnotationRef]] = sorted(
        [(False, a) for a in enclosing_type.annotations]
        + [(True, a) for a in method_decl.annotations],
        key=lambda t: (t[1].name, t[1].qualified, t[0]),
    )
    if any(a.name in {"CodebaseRoute", "CodebaseRoutes"} for _m, a in combined_anns):
        print(
            "[lancedb-mcp] v1 brownfield annotation detected; migrate to "
            "CodebaseHttpRoute / CodebaseAsyncRoute / CodebaseHttpClient",
            file=sys.stderr,
        )

    # ----- Step 2: Layer B — annotation route hints -----
    for _is_m, ann in combined_anns:
        hint = _route_hint_lookup(ann, overrides.annotation_to_route_hint)
        if hint is None:
            continue
        working.append(
            _route_decl_from_route_hint(
                hint,
                method_fqn=method_fqn,
                method_sig=method_decl.signature,
                filename=filename,
                start_line=sl,
                end_line=el,
                source_layer="layer_b_ann",
            ),
        )

    # ----- Step 3: Layer A — meta-linked custom mapping annotations -----
    if meta_chain is not None:
        seen_a: set[tuple[str, str]] = set()
        for _is_m, ann in combined_anns:
            key = (ann.name, ann.qualified)
            if key in seen_a:
                continue
            extra = _layer_a_route_decls_from_ann(
                ann,
                meta_chain,
                method_fqn=method_fqn,
                method_sig=method_decl.signature,
                filename=filename,
                start_line=sl,
                end_line=el,
            )
            if extra:
                seen_a.add(key)
                working.extend(extra)

    # ----- Step 4: Layer C — in-source @CodebaseRoute -----
    working = _merge_layer_c_codebase_routes(working, layer_c_src)

    # ----- Step 5: Layer B — per-type FQN route hint -----
    fh = overrides.fqn_to_route_hint.get(enclosing_type.fqn)
    if fh is not None:
        working = _apply_layer_b_fqn(
            working,
            fh,
            method_fqn=method_fqn,
            method_sig=method_decl.signature,
            filename=filename,
            start_line=sl,
            end_line=el,
        )

    return working


def _client_hint_lookup(
    ann: AnnotationRef,
    hints: dict[str, HttpClientHint],
) -> HttpClientHint | None:
    q = ann.qualified.strip()
    if q in hints:
        return hints[q]
    if ann.name in hints:
        return hints[ann.name]
    for k, h in sorted(hints.items(), key=lambda kv: kv[0]):
        if k.endswith("." + ann.name):
            return h
    return None


def _async_hint_lookup(
    ann: AnnotationRef,
    hints: dict[str, AsyncProducerHint],
) -> AsyncProducerHint | None:
    q = ann.qualified.strip()
    if q in hints:
        return hints[q]
    if ann.name in hints:
        return hints[ann.name]
    for k, h in sorted(hints.items(), key=lambda kv: kv[0]):
        if k.endswith("." + ann.name):
            return h
    return None


def _call_from_http_hint(
    *,
    hint: HttpClientHint,
    base_call: OutgoingCallDecl | None,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    source_layer: str,
) -> OutgoingCallDecl:
    filename = base_call.filename if base_call is not None else ""
    start_line = base_call.start_line if base_call is not None else method_decl.start_line
    end_line = base_call.end_line if base_call is not None else method_decl.end_line
    method_fqn = (
        base_call.method_fqn if base_call is not None else f"{enclosing_type.fqn}#{method_decl.signature}"
    )
    method_sig = base_call.method_sig if base_call is not None else method_decl.signature
    return OutgoingCallDecl(
        method_fqn=method_fqn,
        method_sig=method_sig,
        client_kind=hint.client_kind or (base_call.client_kind if base_call else ""),
        channel="http",
        feign_target_name=hint.target_service or (base_call.feign_target_name if base_call else ""),
        feign_target_url=base_call.feign_target_url if base_call else "",
        path_template_call=hint.path or (base_call.path_template_call if base_call else ""),
        method_call=hint.method or (base_call.method_call if base_call else ""),
        topic_call="",
        broker_call="",
        raw_uri=(base_call.raw_uri if base_call else (hint.path or "")),
        raw_topic="",
        resolution_strategy=source_layer,
        confidence_base=1.0,
        resolved=True,
        filename=filename,
        start_line=start_line,
        end_line=end_line,
    )


def _call_from_async_hint(
    *,
    hint: AsyncProducerHint,
    base_call: OutgoingCallDecl | None,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    source_layer: str,
) -> OutgoingCallDecl:
    filename = base_call.filename if base_call is not None else ""
    start_line = base_call.start_line if base_call is not None else method_decl.start_line
    end_line = base_call.end_line if base_call is not None else method_decl.end_line
    method_fqn = (
        base_call.method_fqn if base_call is not None else f"{enclosing_type.fqn}#{method_decl.signature}"
    )
    method_sig = base_call.method_sig if base_call is not None else method_decl.signature
    return OutgoingCallDecl(
        method_fqn=method_fqn,
        method_sig=method_sig,
        client_kind=hint.client_kind or (base_call.client_kind if base_call else ""),
        channel="async",
        feign_target_name="",
        feign_target_url="",
        path_template_call="",
        method_call="",
        topic_call=hint.topic or (base_call.topic_call if base_call else ""),
        broker_call=hint.broker or (base_call.broker_call if base_call else ""),
        raw_uri="",
        raw_topic=(base_call.raw_topic if base_call else (hint.topic or "")),
        resolution_strategy=source_layer,
        confidence_base=1.0,
        resolved=True,
        filename=filename,
        start_line=start_line,
        end_line=end_line,
    )


def resolve_http_client_for_method(
    *,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, frozenset[str]] | None,
    builtin_calls: list[OutgoingCallDecl],
) -> list[OutgoingCallDecl]:
    builtins_only = [c for c in builtin_calls if c.resolution_strategy != "codebase_client"]
    layer_c_src = [c for c in builtin_calls if c.resolution_strategy == "codebase_client"]
    combined_anns: list[tuple[bool, AnnotationRef]] = sorted(
        [(False, a) for a in enclosing_type.annotations]
        + [(True, a) for a in method_decl.annotations],
        key=lambda t: (t[1].name, t[1].qualified, t[0]),
    )
    builtin_http = [c for c in builtins_only if c.channel == "http"]
    brownfield_calls: list[OutgoingCallDecl] = []
    anchor = builtin_http[0] if builtin_http else (layer_c_src[0] if layer_c_src else None)

    for _is_m, ann in combined_anns:
        hint = _client_hint_lookup(ann, overrides.annotation_to_http_client_hint)
        if hint is None:
            continue
        brownfield_calls.append(
            _call_from_http_hint(
                hint=hint,
                base_call=anchor,
                method_decl=method_decl,
                enclosing_type=enclosing_type,
                source_layer="layer_b_ann",
            ),
        )

    if meta_chain is not None:
        seen_a: set[tuple[str, str]] = set()
        for _is_m, ann in combined_anns:
            key = (ann.name, ann.qualified)
            if key in seen_a:
                continue
            if ann.name in CODEBASE_HTTP_CLIENT_ANNOTATIONS:
                continue
            chain = meta_chain.get(ann.name, frozenset())
            if "CodebaseHttpClient" not in chain and "CodebaseHttpClients" not in chain:
                continue
            hint = overrides.annotation_to_http_client_hint.get("CodebaseHttpClient")
            if hint is None:
                hint = HttpClientHint(
                    client_kind=anchor.client_kind if anchor else "rest_template",
                    target_service=anchor.feign_target_name if anchor else "",
                    path=anchor.path_template_call if anchor else "",
                    method=anchor.method_call if anchor else "",
                )
            seen_a.add(key)
            brownfield_calls.append(
                _call_from_http_hint(
                    hint=hint,
                    base_call=anchor,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_a_meta",
                ),
            )

    for c in layer_c_src:
        if c.channel == "http":
            brownfield_calls.append(replace(c, resolution_strategy="layer_c_source"))

    fh = overrides.fqn_to_http_client_hint.get(enclosing_type.fqn)
    if fh is not None:
        if not brownfield_calls:
            brownfield_calls.append(
                _call_from_http_hint(
                    hint=fh,
                    base_call=anchor,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_b_fqn",
                ),
            )
        else:
            brownfield_calls = [
                _call_from_http_hint(
                    hint=fh,
                    base_call=c,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_b_fqn",
                ) for c in brownfield_calls
            ]
    return brownfield_calls if brownfield_calls else builtin_http


def resolve_async_producer_for_method(
    *,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, frozenset[str]] | None,
    builtin_calls: list[OutgoingCallDecl],
) -> list[OutgoingCallDecl]:
    builtins_only = [c for c in builtin_calls if c.resolution_strategy != "codebase_producer"]
    layer_c_src = [c for c in builtin_calls if c.resolution_strategy == "codebase_producer"]
    combined_anns: list[tuple[bool, AnnotationRef]] = sorted(
        [(False, a) for a in enclosing_type.annotations]
        + [(True, a) for a in method_decl.annotations],
        key=lambda t: (t[1].name, t[1].qualified, t[0]),
    )
    builtin_async = [c for c in builtins_only if c.channel == "async"]
    brownfield_calls: list[OutgoingCallDecl] = []
    anchor = builtin_async[0] if builtin_async else (layer_c_src[0] if layer_c_src else None)

    for _is_m, ann in combined_anns:
        hint = _async_hint_lookup(ann, overrides.annotation_to_async_producer_hint)
        if hint is None:
            continue
        brownfield_calls.append(
            _call_from_async_hint(
                hint=hint,
                base_call=anchor,
                method_decl=method_decl,
                enclosing_type=enclosing_type,
                source_layer="layer_b_ann",
            ),
        )

    if meta_chain is not None:
        seen_a: set[tuple[str, str]] = set()
        for _is_m, ann in combined_anns:
            key = (ann.name, ann.qualified)
            if key in seen_a:
                continue
            if ann.name in CODEBASE_PRODUCER_ANNOTATIONS:
                continue
            chain = meta_chain.get(ann.name, frozenset())
            if "CodebaseProducer" not in chain and "CodebaseProducers" not in chain:
                continue
            hint = overrides.annotation_to_async_producer_hint.get("CodebaseProducer")
            if hint is None:
                hint = AsyncProducerHint(
                    client_kind=anchor.client_kind if anchor else "kafka_send",
                    topic=anchor.topic_call if anchor else "",
                    broker=anchor.broker_call if anchor else "",
                )
            seen_a.add(key)
            brownfield_calls.append(
                _call_from_async_hint(
                    hint=hint,
                    base_call=anchor,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_a_meta",
                ),
            )

    for c in layer_c_src:
        if c.channel == "async":
            brownfield_calls.append(replace(c, resolution_strategy="layer_c_source"))

    fh = overrides.fqn_to_async_producer_hint.get(enclosing_type.fqn)
    if fh is not None:
        if not brownfield_calls:
            brownfield_calls.append(
                _call_from_async_hint(
                    hint=fh,
                    base_call=anchor,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_b_fqn",
                ),
            )
        else:
            brownfield_calls = [
                _call_from_async_hint(
                    hint=fh,
                    base_call=c,
                    method_decl=method_decl,
                    enclosing_type=enclosing_type,
                    source_layer="layer_b_fqn",
                ) for c in brownfield_calls
            ]
    return brownfield_calls if brownfield_calls else builtin_async


def _resolve_with_root(
    file_path: str, project_root: str | Path | None,
) -> tuple[Path, Path | None]:
    p = Path(file_path)
    if project_root is None:
        try:
            return p.resolve(), None
        except OSError:
            return p, None
    root = Path(project_root).resolve()
    try:
        p_abs = (root / p).resolve() if not p.is_absolute() else p.resolve()
    except OSError:
        p_abs = p
    return p_abs, root


def _bounded_parents(p: Path, root: Path | None) -> list[Path]:
    """Parents of `p`, stopping at (and not crossing above) `root`."""
    try:
        parents = list(p.parents)
    except OSError:
        return []
    if root is None:
        return parents
    bounded: list[Path] = []
    for parent in parents:
        bounded.append(parent)
        if parent == root:
            break
    return bounded


def _has_build_marker(directory: Path) -> bool:
    for marker in BUILD_MARKERS:
        if (directory / marker).is_file():
            return True
    return False


def module_for_path(file_path: str, project_root: str | Path | None = None) -> str:
    """Innermost build-marker ancestor's directory name.

    Returns "" when no build marker is found between the file and
    `project_root` (inclusive).
    """
    p, root = _resolve_with_root(file_path, project_root)
    for parent in _bounded_parents(p, root):
        if _has_build_marker(parent):
            return parent.name
    return ""


def microservice_for_path(
    file_path: str, project_root: str | Path | None = None,
) -> str:
    """Outermost build-marker ancestor under `project_root`.

    Resolution order, first hit wins:

    1. Explicit override (env var + config file). The override is a list
       of directory names; the first one that appears in the file's
       ancestry (under `project_root`) wins.
    2. Outermost build-marker ancestor between `project_root` and `file`
       (i.e. the build marker closest to `project_root`).
    3. First path segment under `project_root`.
    4. "" — when none of the above apply (typically: file *is*
       `project_root`, or `project_root` is None and the file path
       has no parents).
    """
    p, root = _resolve_with_root(file_path, project_root)
    parents = _bounded_parents(p, root)

    overrides = load_microservice_overrides(project_root)
    if overrides:
        # Walk from outermost to innermost so a nested override (rare)
        # still works when the user lists a deeper directory.
        override_set = set(overrides)
        for parent in reversed(parents):
            if parent.name in override_set:
                return parent.name
        # Fall through to structural inference if no override matched.

    outermost_marker: Path | None = None
    for parent in parents:
        if _has_build_marker(parent):
            outermost_marker = parent
    if outermost_marker is not None and (root is None or outermost_marker != root):
        return outermost_marker.name

    if root is not None:
        # First path segment under `root`. parents are ordered
        # innermost-first; the candidate is the parent immediately
        # below `root`.
        for parent in parents:
            try:
                rel = parent.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) == 1:
                return parts[0]

    return ""


def detect_microservice_from_path(cwd: Path, source_root: Path) -> str | None:
    """Detect microservice from cwd for query-time auto-scope.

    Returns None if cwd is outside source_root, cwd IS source_root (system level),
    or no microservice is detected. Otherwise returns the microservice name.
    """
    cwd_resolved = cwd.resolve()
    source_resolved = source_root.resolve()

    # Check if cwd is outside source_root
    try:
        cwd_resolved.relative_to(source_resolved)
    except ValueError:
        return None

    # Check if cwd IS source_root (at system level, no specific scope)
    if cwd_resolved == source_resolved:
        return None

    # Check if cwd itself matches a YAML override (directory name matches microservice_roots)
    overrides = load_microservice_overrides(source_resolved)
    if overrides and cwd_resolved.name in overrides:
        return cwd_resolved.name

    # microservice_for_path walks _bounded_parents which excludes the path itself.
    # For query-time detection we need cwd included in the walk, so pass a synthetic
    # child path so that cwd appears as a parent in the build-marker scan.
    synthetic = cwd_resolved / "__scope_probe__"
    ms = microservice_for_path(str(synthetic), source_resolved)
    return ms if ms else None


# ---------- chunk enrichment ----------


def _flatten_types(ast: JavaFileAst) -> list[TypeDecl]:
    return list(ast.all_types)


def _enclosing_type(ast: JavaFileAst, start: int, end: int) -> TypeDecl | None:
    """Smallest TypeDecl whose [start_byte, end_byte] contains chunk range.

    Falls back to largest overlap if nothing fully encloses.
    """
    best: TypeDecl | None = None
    best_span = -1
    for t in _flatten_types(ast):
        if t.start_byte <= start and end <= t.end_byte:
            span = t.end_byte - t.start_byte
            if best is None or span < best_span or best_span < 0:
                best = t
                best_span = span
    if best is not None:
        return best

    overlap_best: TypeDecl | None = None
    overlap_size = 0
    for t in _flatten_types(ast):
        o = max(0, min(end, t.end_byte) - max(start, t.start_byte))
        if o > overlap_size:
            overlap_size = o
            overlap_best = t
    return overlap_best


def _symbols_in_range(ast: JavaFileAst, start: int, end: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in _flatten_types(ast):
        if t.end_byte < start or t.start_byte > end:
            continue
        if start <= t.start_byte <= end and t.name not in seen:
            out.append(t.name)
            seen.add(t.name)
        for f in t.fields:
            if start <= f.start_byte <= end and f.name not in seen:
                out.append(f.name)
                seen.add(f.name)
        for m in t.methods:
            if start <= m.start_byte <= end and m.name not in seen:
                out.append(m.name)
                seen.add(m.name)
    return out


def enrich_chunk(
    ast: JavaFileAst,
    *,
    chunk_start_byte: int,
    chunk_end_byte: int,
    file_path: str,
    project_root: str | Path | None = None,
) -> ChunkEnrichment:
    """Compute enrichment metadata for a single chunk of a parsed Java file."""
    module = module_for_path(file_path, project_root)
    microservice = microservice_for_path(file_path, project_root)
    encl = _enclosing_type(ast, chunk_start_byte, chunk_end_byte)
    if encl is not None:
        ann_names = [a.name for a in encl.annotations]
        prs: str | None = None
        if project_root is not None:
            try:
                prs = str(Path(project_root).resolve())
            except OSError:
                prs = str(project_root)
        bov = load_brownfield_overrides(project_root)
        mchain = collect_annotation_meta_chain(prs) if prs else None
        role, cap_list = resolve_role_and_capabilities(
            encl,
            overrides=bov,
            meta_chain=mchain,
        )
        return ChunkEnrichment(
            package=ast.package,
            module=module,
            microservice=microservice,
            primary_type_fqn=encl.fqn,
            primary_type_kind=encl.kind,
            role=role,
            annotations_on_type=ann_names,
            symbols=_symbols_in_range(ast, chunk_start_byte, chunk_end_byte),
            capabilities=cap_list,
        )
    return ChunkEnrichment(
        package=ast.package,
        module=module,
        microservice=microservice,
        primary_type_fqn="",
        primary_type_kind="",
        role="OTHER",
        annotations_on_type=[],
        symbols=_symbols_in_range(ast, chunk_start_byte, chunk_end_byte),
        capabilities=[],
    )


def symbol_id(kind: str, fqn: str, file_path: str = "", start_byte: int = 0) -> str:
    """Deterministic SHA1-based id for LadybugDB Symbol nodes."""
    key = f"{kind}|{fqn}|{file_path}|{start_byte}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()


def phantom_id(simple_or_fqn: str) -> str:
    """Id for unresolved/external type targets (phantom Symbol rows)."""
    key = f"class|__phantom.{simple_or_fqn}|".encode("utf-8")
    return hashlib.sha1(key).hexdigest()
