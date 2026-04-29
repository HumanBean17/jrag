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
    1. explicit override list (env var or config file at project root);
    2. outermost build marker between `project_root` and the file;
    3. first path segment under `project_root`;
    4. empty.
"""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from ast_java import (
    JavaFileAst,
    TypeDecl,
    infer_capabilities_for_type,
    infer_role_for_type,
    parse_java,
    ROLE_ANNOTATIONS,
    _METHOD_ANN_TO_CAPABILITY,
    _TYPE_ANN_TO_CAPABILITY,
)
from java_ontology import VALID_CAPABILITIES, VALID_ROLES
from java_index_v1_common import (
    COMMON_EXCLUDED_PATH_PATTERNS,
    compile_excluded_glob_patterns,
    iter_java_source_files,
)

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
    "symbol_id",
    "phantom_id",
    "BUILD_MARKERS",
    "MICROSERVICE_ROOTS_ENV",
    "CONFIG_FILENAMES",
]

BUILD_MARKERS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
)

MICROSERVICE_ROOTS_ENV = "LANCEDB_MCP_MICROSERVICE_ROOTS"

# Recognised config filenames at `project_root` (first match wins).
CONFIG_FILENAMES = (".lancedb-mcp.yml", ".lancedb-mcp.yaml")


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
    """Read `microservice_roots` from `.lancedb-mcp.yml` at project_root.

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


def load_microservice_overrides(project_root: str | Path | None) -> tuple[str, ...]:
    """Combined override list (env var ++ config file).

    Env var `LANCEDB_MCP_MICROSERVICE_ROOTS` takes precedence; both
    sources are merged in declaration order, deduplicated.
    """
    out: list[str] = []
    seen: set[str] = set()

    env_raw = os.environ.get(MICROSERVICE_ROOTS_ENV, "").strip()
    for name in _parse_csv(env_raw):
        if name not in seen:
            seen.add(name)
            out.append(name)

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
class BrownfieldOverrides:
    annotation_to_role: dict[str, str]
    annotation_to_capabilities: dict[str, tuple[str, ...]]
    fqn_role: dict[str, str]
    fqn_capabilities: dict[str, tuple[str, ...]]


def _meta_builtins() -> frozenset[str]:
    return frozenset(ROLE_ANNOTATIONS) | frozenset(
        _METHOD_ANN_TO_CAPABILITY
    ) | frozenset(_TYPE_ANN_TO_CAPABILITY)


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
    excludes = compile_excluded_glob_patterns(COMMON_EXCLUDED_PATH_PATTERNS)
    decls: dict[str, AnnotationDecl] = {}
    for p in sorted(iter_java_source_files(root, excludes), key=str):
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

    Single source of truth for Layer A: both the Kuzu writer and Lance chunk
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
    (disk) so Kuzu and Lance share one index.
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
    """Read `role_overrides` from `.lancedb-mcp.yml` at project_root. Cached per root."""
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
            return BrownfieldOverrides(
                {},
                {},
                {},
                {},
            )
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            return BrownfieldOverrides(
                {},
                {},
                {},
                {},
            )
        if not isinstance(data, dict):
            return BrownfieldOverrides(
                {},
                {},
                {},
                {},
            )
        ro = data.get("role_overrides")
        if not isinstance(ro, dict):
            return BrownfieldOverrides(
                {},
                {},
                {},
                {},
            )
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
        return BrownfieldOverrides(
            a_to_r,
            a_to_c,
            fqn_r,
            fqn_c,
        )
    return BrownfieldOverrides(
        {},
        {},
        {},
        {},
    )


def load_brownfield_overrides(
    project_root: str | Path | None,
) -> BrownfieldOverrides:
    if project_root is None:
        return BrownfieldOverrides(
            {},
            {},
            {},
            {},
        )
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
    """Deterministic SHA1-based id for Kuzu Symbol nodes."""
    key = f"{kind}|{fqn}|{file_path}|{start_byte}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()


def phantom_id(simple_or_fqn: str) -> str:
    """Id for unresolved/external type targets (phantom Symbol rows)."""
    key = f"class|__phantom.{simple_or_fqn}|".encode("utf-8")
    return hashlib.sha1(key).hexdigest()
