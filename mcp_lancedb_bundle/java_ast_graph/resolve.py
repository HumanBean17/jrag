"""Pass-2: resolve raw type strings to FQNs using imports, same-package, and global registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from java_ast_graph.extract import FileFact


@dataclass
class SymbolRegistry:
    """All type FQNs discovered in pass 1."""

    fqns: set[str] = field(default_factory=set)
    simple_to_fqns: dict[str, list[str]] = field(default_factory=dict)

    def add_fqn(self, fqn: str, simple: str) -> None:
        self.fqns.add(fqn)
        self.simple_to_fqns.setdefault(simple, []).append(fqn)

    def finish(self) -> None:
        for k in self.simple_to_fqns:
            self.simple_to_fqns[k] = sorted(set(self.simple_to_fqns[k]))


def build_registry(file_facts: list[FileFact]) -> SymbolRegistry:
    reg = SymbolRegistry()
    for ff in file_facts:
        if ff.error:
            continue
        for t in ff.types:
            reg.add_fqn(t.fqn, t.simple_name)
    reg.finish()
    return reg


def _split_generics(raw: str) -> str:
    s = raw.strip()
    if "<" in s:
        s = s[: s.index("<")].strip()
    return s


def _first_segment(raw: str) -> str:
    s = _split_generics(raw)
    if "." in s:
        return s.split(".")[-1]
    return s


def resolve_type_ref(
    raw: str,
    package: str,
    import_ctx: object,
    registry: SymbolRegistry,
) -> tuple[str | None, bool]:
    """
    Return (resolved_fqn, ambiguous) or (None, False) if unresolved.
    """
    from java_ast_graph.parseutil import ImportContext

    assert isinstance(import_ctx, ImportContext)
    t = _split_generics(raw).strip()
    if not t:
        return None, False
    if t.endswith("[]"):
        t = t[:-2].strip()
    if t in registry.fqns:
        return t, False
    simple = _first_segment(t) if t else t
    if simple in import_ctx.single:
        fqn = import_ctx.single[simple]
        if fqn in registry.fqns:
            return fqn, False
        return fqn, False
    same_pkg = f"{package}.{simple}" if package else simple
    if same_pkg in registry.fqns:
        return same_pkg, False
    cands = registry.simple_to_fqns.get(simple, [])
    if len(cands) == 1:
        return cands[0], False
    if len(cands) > 1:
        for sp in import_ctx.star_packages:
            for c in cands:
                if c.startswith(sp + ".") or c.rsplit(".", 1)[0] == sp:
                    return c, False
        return None, True
    for sp in import_ctx.star_packages:
        guess = f"{sp}.{simple}"
        if guess in registry.fqns:
            return guess, False
    return None, False


@dataclass
class GraphEdges:
    extends: list[tuple[str, str, bool]]  # (from_fqn, to_fqn, resolved)
    implements: list[tuple[str, str, bool]]
    injects: list[tuple[str, str, bool]]


def build_edges(
    file_facts: list[FileFact],
    registry: SymbolRegistry,
) -> GraphEdges:
    ex: list[tuple[str, str, bool]] = []
    im: list[tuple[str, str, bool]] = []
    inj: list[tuple[str, str, bool]] = []

    for ff in file_facts:
        if ff.error:
            continue
        ictx = ff.import_ctx
        for t in ff.types:
            if t.extends_raw:
                tgt, amb = resolve_type_ref(
                    t.extends_raw, ff.package, ictx, registry
                )
                if tgt:
                    ex.append((t.fqn, tgt, True))
            for raw in t.implements_raw:
                tgt, _ = resolve_type_ref(raw, ff.package, ictx, registry)
                if tgt:
                    im.append((t.fqn, tgt, True))
            for from_f, raw in t.field_injections:
                tgt, _ = resolve_type_ref(raw, ff.package, ictx, registry)
                if tgt:
                    inj.append((from_f, tgt, True))
            for from_f, raw in t.constructor_injections:
                tgt, _ = resolve_type_ref(raw, ff.package, ictx, registry)
                if tgt:
                    inj.append((from_f, tgt, True))
    return GraphEdges(extends=ex, implements=im, injects=inj)
