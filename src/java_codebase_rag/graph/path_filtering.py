"""Layered path ignore rules for Java indexing and graph enrichment (B5).

Resolution order (later overrides earlier; innermost nested wins among peers):

1. ``builtin_default`` — legacy ``COMMON_EXCLUDED_PATH_PATTERNS`` (gitignore-style).
2. ``project_root`` — ``<project>/.java-codebase-rag/ignore``.
3. ``nested`` — each ``<dir>/.java-codebase-rag/ignore`` along the path from project root
   to the file's parent (outer dirs first, inner dirs last).
4. ``gitignore`` — each ``.gitignore`` from project root down to the file's parent
   (when ``use_gitignore`` is true), using :class:`pathspec.GitIgnoreSpec`.

Paths outside ``project_root`` are never ignored by this object.
"""
from __future__ import annotations

import fnmatch
import os
import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import overload

from pathspec import GitIgnoreSpec

from java_codebase_rag.ast.language import LANG_BACKENDS

# Pruning for LocalFile sources: skip VCS, build outputs, dependency trees, and
# test sources (we currently index prod Java only to keep the semantic index clean).
# Also avoids EMFILE under default ulimits when the engine traverses in parallel.
#
# Note on build-output dir names: ``out``, ``build`` and ``target`` are also legal
# Java package names (e.g. ``com.example.out.api``). The unconditional ``**/out/**``
# pattern that previously lived here false-matched such packages and silently
# dropped real source files. These dirs are now pruned only when they sit next to
# a build-tool indicator (``pom.xml``, ``build.gradle``, ``build.gradle.kts``,
# ``settings.gradle``, ``settings.gradle.kts``) — see ``_is_build_output_dir``
# and ``BUILD_DIR_NAMES``. If you genuinely need to skip an arbitrary nested
# directory, add a ``.java-codebase-rag/ignore`` entry at the project or subtree root.
COMMON_EXCLUDED_PATH_PATTERNS: list[str] = [
    "**/.*",
    "**/.git/**",
    "**/.idea/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/*.class",
    "**/src/test/java/**",
    "**/src/test/resources/**",
]

# Directory names that are pruned ONLY when they sit next to a build-tool indicator.
# The check is ``parent_dir`` contains any of ``BUILD_TOOL_INDICATORS``.
BUILD_DIR_NAMES: tuple[str, ...] = ("target", "build", "out")

# Files whose presence in a directory marks it as a JVM build module. When one
# of these sits next to a ``BUILD_DIR_NAMES`` entry, that entry is treated as
# build output and pruned from the walk.
BUILD_TOOL_INDICATORS: tuple[str, ...] = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)

# Directory names always pruned regardless of siblings (universal nuisance dirs;
# never a legal package name in practice).
UNCONDITIONAL_PRUNE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".idea",
    ".venv",
    "node_modules",
})


def _is_build_output_dir(parent_dir: str, dirname: str) -> bool:
    """True iff ``<parent_dir>/<dirname>`` looks like a JVM build-output directory.

    A name in :data:`BUILD_DIR_NAMES` is build output only when its parent
    directory contains a build-tool indicator (Maven/Gradle marker file).
    Otherwise, names like ``out`` are treated as ordinary subdirectories so
    Java sources under packages such as ``com.example.out.api`` survive the walk.
    """
    if dirname not in BUILD_DIR_NAMES:
        return False
    try:
        with os.scandir(parent_dir) as it:
            siblings = {entry.name for entry in it}
    except OSError:
        return False
    return any(marker in siblings for marker in BUILD_TOOL_INDICATORS)


def compile_excluded_glob_patterns(
    patterns: Sequence[str] | tuple[str, ...],
) -> list[str]:
    """Store exclude patterns in list form; same as ast-graph ``index`` compile step."""
    return list(patterns)


def is_relative_path_excluded(rel_posix: str, exclude_globs: list[str]) -> bool:
    """True if a project-relative path matches an exclude glob (incl. ``**/<path>``)."""
    for pat in exclude_globs:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        if fnmatch.fnmatch(f"**/{rel_posix}", pat):
            return True
    return False


@dataclass(frozen=True)
class IgnoreLayer:
    """One ignore configuration anchored at ``root`` (patterns apply under this dir)."""

    root: Path
    spec: GitIgnoreSpec
    source: str
    ignore_file: Path | None = None


def _read_ignore_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()


def _line_has_negation(lines: Sequence[str]) -> bool:
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("\\!"):
            continue
        if s.startswith("!"):
            return True
    return False


def _scan_negation_any_bundle_ignore(project_root: Path) -> bool:
    """Return True if any ``.java-codebase-rag/ignore`` contains a negation (``!``) line.

    Runs one ``rglob`` at :class:`LayeredIgnore` construction. Fine for typical
    repos; very large monorepos pay a full-tree walk on every new ``LayeredIgnore``
    instance (same for :func:`_scan_negation_any_gitignore`).
    """
    root = project_root.resolve()
    try:
        for p in root.rglob(".java-codebase-rag"):
            if not p.is_dir():
                continue
            ign = p / "ignore"
            if ign.is_file() and _line_has_negation(_read_ignore_lines(ign)):
                return True
    except OSError:
        return False
    return False


def _scan_negation_any_gitignore(project_root: Path) -> bool:
    """See :func:`_scan_negation_any_bundle_ignore` (also uses ``rglob``)."""
    root = project_root.resolve()
    try:
        for p in root.rglob(".gitignore"):
            if p.is_file() and _line_has_negation(_read_ignore_lines(p)):
                return True
    except OSError:
        return False
    return False


def _prefix_line_to_project(
    prefix_posix: str,
    raw_line: str,
) -> str | None:
    """Map a gitignore line from a subdirectory anchor to project-root-relative."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    neg = line.startswith("!")
    body = line[1:] if neg else line
    if body.startswith("\\#") or body.startswith("\\!"):
        body = body[1:]
    anchored = body.startswith("/")
    if anchored:
        body = body[1:]
    if prefix_posix:
        mapped = f"{prefix_posix}/{body}" if body else prefix_posix
    else:
        mapped = body
    return f"!{mapped}" if neg else mapped


def _mega_build_for_rel(
    self_root: Path,
    rel_project: str,
    *,
    use_gitignore: bool,
    builtin_lines: list[str],
    project_ignore_path: Path,
    project_lines: list[str] | None,
) -> tuple[list[str], list[tuple[str, Path | None, int, str]]]:
    """Mega gitignore lines (project-relative) + (source, file, line_no, pattern_text)."""
    mega: list[str] = []
    meta: list[tuple[str, Path | None, int, str]] = []

    def extend_builtin() -> None:
        for i, raw in enumerate(builtin_lines, start=1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            mega.append(raw.rstrip("\n"))
            meta.append(("builtin_default", None, i, s))

    def extend_file(source: str, path: Path, lines: Sequence[str]) -> None:
        for lineno, raw in enumerate(lines, start=1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            mega.append(raw.rstrip("\n"))
            meta.append((source, path, lineno, s))

    extend_builtin()
    if project_lines is not None:
        extend_file("project_root", project_ignore_path, project_lines)

    parts = Path(rel_project).parts
    dir_parts = parts[:-1] if len(parts) > 1 else ()
    for i in range(1, len(dir_parts) + 1):
        anchor = self_root.joinpath(*dir_parts[:i])
        nested_path = anchor / ".java-codebase-rag" / "ignore"
        if not nested_path.is_file():
            continue
        prefix = anchor.relative_to(self_root).as_posix()
        nlines = _read_ignore_lines(nested_path)
        for lineno, raw in enumerate(nlines, start=1):
            mapped = _prefix_line_to_project(prefix, raw)
            if mapped is None:
                continue
            mega.append(mapped)
            meta.append(("nested", nested_path, lineno, raw.strip()))

    if use_gitignore:
        for i in range(len(dir_parts) + 1):
            anchor = self_root if i == 0 else self_root.joinpath(*dir_parts[:i])
            git_path = anchor / ".gitignore"
            if not git_path.is_file():
                continue
            prefix = anchor.relative_to(self_root).as_posix() if i > 0 else ""
            glines = _read_ignore_lines(git_path)
            for lineno, raw in enumerate(glines, start=1):
                mapped = _prefix_line_to_project(prefix, raw)
                if mapped is None:
                    continue
                mega.append(mapped)
                meta.append(("gitignore", git_path, lineno, raw.strip()))

    return mega, meta


def _winning_row(
    rel: str,
    mega: list[str],
    meta: list[tuple[str, Path | None, int, str]],
) -> tuple[str, Path | None, int, str]:
    """The last rule line that changes the cumulative match result (git semantics)."""
    if not mega:
        return "builtin_default", None, 1, ""
    state = False
    last_idx = 0
    for i in range(len(mega)):
        cur = GitIgnoreSpec.from_lines(mega[: i + 1]).match_file(rel)
        if cur != state:
            last_idx = i
            state = cur
    return meta[last_idx]


class LayeredIgnore:
    """Evaluate layered ignore rules anchored at a single project root."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        use_gitignore: bool = True,
        builtin_patterns: Sequence[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.use_gitignore = use_gitignore
        self._builtin_lines = (
            list(builtin_patterns)
            if builtin_patterns is not None
            else list(COMMON_EXCLUDED_PATH_PATTERNS)
        )
        self._project_ignore_path = self.project_root / ".java-codebase-rag" / "ignore"
        self._project_lines: list[str] | None = None
        if self._project_ignore_path.is_file():
            self._project_lines = _read_ignore_lines(self._project_ignore_path)
        self._permissive_coco_walk = (
            _scan_negation_any_bundle_ignore(self.project_root)
            or (use_gitignore and _scan_negation_any_gitignore(self.project_root))
        )
        self._mega_cache: dict[str, tuple[list[str], GitIgnoreSpec, list[tuple[str, Path | None, int, str]]]] = {}

    def cocoindex_excluded_patterns(self) -> list[str]:
        """Patterns for CocoIndex ``PatternFilePathMatcher.excluded_patterns``.

        Matches pre-B5 behaviour when no negation rules exist anywhere under the
        project that could un-ignore paths under pruned directories. Otherwise
        returns an empty list and callers must filter each path with
        :meth:`is_ignored`.
        """
        if self._permissive_coco_walk:
            return []
        return list(self._builtin_lines)

    def _rel_project(self, path: Path) -> str | None:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return None

    def _path_for_display(self, path: Path | None) -> str:
        """Project-relative POSIX path when under ``project_root``; else best-effort short path."""
        if path is None:
            return ""
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            try:
                return path.resolve().relative_to(Path.cwd()).as_posix()
            except ValueError:
                return path.as_posix()

    def _mega(self, rel_project: str) -> tuple[list[str], GitIgnoreSpec, list[tuple[str, Path | None, int, str]]]:
        # Cache by directory (parent of rel_project). _mega_build_for_rel reads only dir_parts,
        # so files in the same directory share the same mega/spec/meta tuple.
        cache_key = Path(rel_project).parent.as_posix()
        if cache_key in self._mega_cache:
            return self._mega_cache[cache_key]
        mega, meta = _mega_build_for_rel(
            self.project_root,
            rel_project,
            use_gitignore=self.use_gitignore,
            builtin_lines=self._builtin_lines,
            project_ignore_path=self._project_ignore_path,
            project_lines=self._project_lines,
        )
        result = (mega, GitIgnoreSpec.from_lines(mega), meta)
        self._mega_cache[cache_key] = result
        return result

    def is_ignored(self, path: Path) -> bool:
        """Return whether ``path`` is ignored by any configured layer.

        Boolean-only fast path for the per-file index walk. It deliberately does
        not compute *which* layer/source last matched: that attribution is
        O(rules²) via :func:`_winning_row` (one ``GitIgnoreSpec`` rebuild per
        rule prefix) and is only needed for ``diagnose-ignore``, so it lives in
        :meth:`diagnose_dict` and is never paid on the hot path.
        """
        rel = self._rel_project(path)
        if rel is None:
            return False
        mega, spec, _ = self._mega(rel)
        if not mega:
            return False
        return spec.match_file(rel)

    def diagnose(self, path: Path) -> str:
        """Human-readable, multi-line explanation of the ignore decision."""
        d = self.diagnose_dict(path)
        expl = d.get("explanation", "")
        layer = d.get("layer")
        ign = d.get("ignored")
        mp = d.get("matching_pattern")
        lines = [
            f"ignored={ign}",
            f"layer={layer!r}",
            f"matching_pattern={mp!r}",
            str(expl),
        ]
        return "\n".join(lines)

    def diagnose_dict(self, path: Path) -> dict[str, object]:
        """Structured diagnose payload for MCP ``diagnose_ignore``."""
        rel = self._rel_project(path)
        if rel is None:
            return {
                "ignored": False,
                "layer": None,
                "matching_pattern": None,
                "explanation": (
                    f"Path {self._path_for_display(path)!r} is outside the configured "
                    "project root — not ignored."
                ),
            }
        mega, spec, meta = self._mega(rel)
        if not mega:
            return {
                "ignored": False,
                "layer": None,
                "matching_pattern": None,
                "explanation": f"Path {rel!r} is not ignored by any configured layer.",
            }
        ignored = spec.match_file(rel)
        if not ignored:
            return {
                "ignored": False,
                "layer": None,
                "matching_pattern": None,
                "explanation": f"Path {rel!r} is not ignored by any configured layer.",
            }
        src, fp, ln, pat = _winning_row(rel, mega, meta)
        if fp is not None:
            expl = (
                f"Excluded by {self._path_for_display(fp)} ({src}) at line {ln}: {pat!r}"
            )
        else:
            expl = f"Excluded by builtin default ({src}) at builtin line {ln}: {pat!r}"
        return {
            "ignored": True,
            "layer": src,
            "matching_pattern": pat,
            "explanation": expl,
        }


@overload
def iter_source_files(root: Path, exclude_globs: list[str]) -> Iterator[Path]: ...


@overload
def iter_source_files(root: Path, *, ignore: LayeredIgnore) -> Iterator[Path]: ...


def iter_source_files(
    root: Path,
    exclude_globs: list[str] | None = None,
    *,
    ignore: LayeredIgnore | None = None,
) -> Iterator[Path]:
    """Walk ``root`` for source files of any registered language backend.

    Yields files whose suffix is claimed by at least one backend in
    :data:`~java_codebase_rag.ast.language.LANG_BACKENDS` (``.java`` always;
    ``.kt`` when the ``tree-sitter-kotlin`` grammar imports). Pruning
    (``UNCONDITIONAL_PRUNE_DIRS`` + ``_is_build_output_dir``) and the layered
    ignore rules are unchanged from the former Java-only walk.
    """
    if exclude_globs is not None and ignore is not None:
        raise TypeError("pass either exclude_globs or ignore=, not both")
    if exclude_globs is not None:
        warnings.warn(
            "iter_source_files(root, exclude_globs) is deprecated; "
            "use iter_source_files(root, ignore=LayeredIgnore(root, ...)).",
            DeprecationWarning,
            stacklevel=2,
        )
        ignore_ctx = LayeredIgnore(root, builtin_patterns=exclude_globs, use_gitignore=False)
    elif ignore is not None:
        ignore_ctx = ignore
    else:
        ignore_ctx = LayeredIgnore(root)
    # Union of suffixes claimed by every registered backend (``.java`` always;
    # ``.kt`` when the Kotlin grammar imports). Computed once per call.
    known_suffixes: set[str] = set()
    for backend in LANG_BACKENDS.values():
        known_suffixes.update(backend.suffixes)
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Universal nuisance dirs (VCS, IDE, deps) are pruned unconditionally.
        # Build-output dirs (``out`` / ``build`` / ``target``) are pruned only when
        # they sit alongside a build-tool indicator file — otherwise names like
        # ``out`` belong to a Java package (e.g. ``com.example.out.api``) and must
        # be walked. See ``_is_build_output_dir``.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in UNCONDITIONAL_PRUNE_DIRS
            and not _is_build_output_dir(dirpath, d)
        ]
        for fn in filenames:
            # ``Path.suffix`` lower-cases nothing; backends register lower-case
            # suffixes (``.java``), matching case-sensitively as before.
            if Path(fn).suffix not in known_suffixes:
                continue
            p = Path(dirpath) / fn
            if ignore_ctx.is_ignored(p):
                continue
            yield p


def iter_java_source_files(
    root: Path,
    exclude_globs: list[str] | None = None,
    *,
    ignore: LayeredIgnore | None = None,
) -> Iterator[Path]:
    """Deprecated alias for :func:`iter_source_files`.

    Kept so any import site not migrated to the new name continues to work; new
    call sites should use :func:`iter_source_files`.
    """
    return iter_source_files(root, exclude_globs, ignore=ignore)
