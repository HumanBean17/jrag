"""Tree-sitter Java helpers: imports, type text, annotations."""

from __future__ import annotations

from dataclasses import dataclass, field
from tree_sitter import Node


@dataclass
class ImportInfo:
    """Single-type import: simple name -> fully qualified name."""

    simple: str
    fqn: str


@dataclass
class ImportContext:
    single: dict[str, str] = field(default_factory=dict)
    star_packages: list[str] = field(default_factory=list)

    def add_type_import(self, simple: str, fqn: str) -> None:
        self.single[simple] = fqn

    def add_star(self, package: str) -> None:
        if package and package not in self.star_packages:
            self.star_packages.append(package)


def _scoped_identifier_to_str(n: Node, src: bytes) -> str:
    return src[n.start_byte : n.end_byte].decode("utf-8", errors="replace")


def parse_import_declaration(imp: Node, src: bytes, ctx: ImportContext) -> None:
    if imp.type != "import_declaration":
        return
    text = src[imp.start_byte : imp.end_byte].decode("utf-8", errors="replace").strip()
    if text.startswith("import static") or "import static" in text[:20]:
        return
    toks = text.replace("\n", " ").split()
    if not toks or toks[0] != "import":
        return
    if ".*" in text:
        rest = text.replace("import", "").replace(";", "").strip()
        rest = rest.replace(".*", "").strip()
        if rest:
            ctx.add_star(rest)
        return
    for c in imp.children:
        if c.type == "scoped_identifier":
            fqn = _scoped_identifier_to_str(c, src)
            simple = fqn.rsplit(".", 1)[-1]
            ctx.add_type_import(simple, fqn)
            return


def collect_imports(program: Node, src: bytes) -> ImportContext:
    ctx = ImportContext()
    for ch in program.children:
        if ch.type == "import_declaration":
            parse_import_declaration(ch, src, ctx)
    return ctx


def type_list_to_type_nodes(type_list: Node) -> list[Node]:
    if type_list is None or type_list.type not in (
        "type_list",
    ):
        return []
    out: list[Node] = []
    for c in type_list.children:
        if c.type == "," or c.type == "extends" or c.type == "implements":
            continue
        if c.type in (
            "type_identifier",
            "scoped_type_identifier",
            "generic_type",
            "array_type",
        ):
            out.append(c)
    return out


def type_node_to_text(n: Node, src: bytes) -> str:
    if n is None:
        return ""
    return src[n.start_byte : n.end_byte].decode("utf-8", errors="replace").strip()


def get_superclass_type_node(class_node: Node) -> Node | None:
    if class_node.type != "class_declaration":
        return None
    sup = class_node.child_by_field_name("superclass")
    if not sup or sup.type != "superclass":
        return None
    for c in sup.children:
        if c.type in (
            "type_identifier",
            "scoped_type_identifier",
            "generic_type",
            "array_type",
        ):
            return c
    for c in sup.children:
        if c.type not in ("extends",) and c.children:
            for cc in c.children:
                if cc.type in (
                    "type_identifier",
                    "scoped_type_identifier",
                ):
                    return cc
    return None


def get_implements_type_list(class_node: Node) -> list[Node]:
    n = class_node.child_by_field_name("interfaces")
    if n is None:
        return []
    for c in n.children:
        if c.type == "type_list":
            return type_list_to_type_nodes(c)
    return []


def get_extends_type_list_for_interface(if_node: Node) -> list[Node]:
    for c in if_node.children:
        if c.type == "extends_interfaces":
            for cc in c.children:
                if cc.type == "type_list":
                    return type_list_to_type_nodes(cc)
    return []


def modifiers_text(modifiers: Node | None, src: bytes) -> str:
    if not modifiers:
        return ""
    if modifiers.type == "modifiers":
        return type_node_to_text(modifiers, src)
    return type_node_to_text(modifiers, src)


def is_autowired_field(field_node: Node, src: bytes) -> bool:
    m = field_node.child_by_field_name("modifiers")
    if not m:
        return False
    mt = type_node_to_text(m, src)
    return "Autowired" in mt or "javax.inject.Inject" in mt or "jakarta.inject.Inject" in mt


def extract_primary_injectable_type(field_type: Node, src: bytes) -> str:
    t = field_type
    if t is None:
        return ""
    if t.type in ("type_identifier", "scoped_type_identifier"):
        return type_node_to_text(t, src)
    if t.type == "array_type":
        el = t.child_by_field_name("element")
        return extract_primary_injectable_type(el, src) if el else type_node_to_text(t, src)
    if t.type == "generic_type":
        args = t.child_by_field_name("type_arguments")
        if args:
            for c in args.children:
                if c.type in (
                    "type_identifier",
                    "scoped_type_identifier",
                    "generic_type",
                ):
                    s = type_node_to_text(c, src)
                    if s and s not in (
                        "String",
                        "Object",
                        "Class",
                    ):
                        if "<" in s:
                            inners = c.children if hasattr(c, "children") else []
                            for ic in c.children or []:
                                if ic.type in (
                                    "type_identifier",
                                    "scoped_type_identifier",
                                ):
                                    return type_node_to_text(ic, src)
                        else:
                            return s
        for c in t.children:
            if c.type in (
                "type_identifier",
                "scoped_type_identifier",
            ):
                return type_node_to_text(c, src)
    return type_node_to_text(t, src)
