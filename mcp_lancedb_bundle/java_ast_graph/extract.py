"""Pass-1: per-file Tree-sitter facts (types, methods, raw type refs, inject hints)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_java as tj

from java_ast_graph.parseutil import (
    ImportContext,
    collect_imports,
    extract_primary_injectable_type,
    get_extends_type_list_for_interface,
    get_implements_type_list,
    get_superclass_type_node,
    is_autowired_field,
    type_node_to_text,
)


@dataclass
class MethodFact:
    name: str
    start_line: int
    is_constructor: bool
    signature_text: str


@dataclass
class TypeFact:
    fqn: str
    kind: str
    simple_name: str
    file_key: str
    file_path: str
    module_root: str
    module_label: str
    extends_raw: str | None = None
    implements_raw: list[str] = field(default_factory=list)
    field_injections: list[tuple[str, str]] = field(default_factory=list)
    constructor_injections: list[tuple[str, str]] = field(default_factory=list)
    methods: list[MethodFact] = field(default_factory=list)


@dataclass
class FileFact:
    file_key: str
    rel_path: str
    module_root: str
    module_label: str
    package: str
    import_ctx: ImportContext
    types: list[TypeFact] = field(default_factory=list)
    error: str | None = None


_DECL_TYPES = (
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
)


def _parser() -> Parser:
    return Parser(Language(tj.language()))


def _type_kind_for_decl(node: Node) -> str:
    if node.type == "class_declaration":
        return "class"
    if node.type == "interface_declaration":
        return "interface"
    if node.type == "enum_declaration":
        return "enum"
    if node.type == "record_declaration":
        return "record"
    if node.type == "annotation_type_declaration":
        return "annotation"
    return "class"


def _line(node: Node) -> int:
    return int(node.start_point[0]) + 1


def _add_methods_to_type(type_node: Node, tf: TypeFact, src: bytes) -> None:
    body = type_node.child_by_field_name("body")
    if not body:
        return
    for ch in body.children:
        if ch.type == "method_declaration":
            name_n = ch.child_by_field_name("name")
            name = (
                src[name_n.start_byte : name_n.end_byte].decode("utf-8", errors="replace")
                if name_n
                else "method"
            )
            tf.methods.append(
                MethodFact(
                    name=name,
                    start_line=_line(ch),
                    is_constructor=False,
                    signature_text=type_node_to_text(ch, src)[:240],
                )
            )
        elif ch.type == "constructor_declaration":
            name_n = ch.child_by_field_name("name")
            name = (
                src[name_n.start_byte : name_n.end_byte].decode("utf-8", errors="replace")
                if name_n
                else "<init>"
            )
            tf.methods.append(
                MethodFact(
                    name=name,
                    start_line=_line(ch),
                    is_constructor=True,
                    signature_text=type_node_to_text(ch, src)[:240],
                )
            )
            params: Node | None = ch.child_by_field_name("parameters")
            if params is None:
                for c2 in ch.children:
                    if c2.type == "formal_parameters":
                        params = c2
                        break
            if params:
                for p in params.children:
                    if p.type != "formal_parameter":
                        continue
                    tnode2 = p.child_by_field_name("type")
                    if not tnode2:
                        continue
                    raw2 = extract_primary_injectable_type(tnode2, src)
                    if raw2 in (
                        "int",
                        "long",
                        "boolean",
                        "byte",
                        "char",
                        "float",
                        "double",
                        "short",
                        "void",
                    ):
                        continue
                    if raw2:
                        tf.constructor_injections.append((tf.fqn, raw2))


def _collect_declarations_recursive(
    node: Node,
    src: bytes,
    package: str,
    file_key: str,
    rel: str,
    mod_root: str,
    mod_label: str,
    outer_fqn: str | None,
) -> list[TypeFact]:
    if node.type not in _DECL_TYPES:
        return []
    name_n = node.child_by_field_name("name")
    if not name_n or name_n.type != "identifier":
        return []
    simple = src[name_n.start_byte : name_n.end_byte].decode("utf-8", errors="replace")
    fqn = f"{outer_fqn}.{simple}" if outer_fqn else (f"{package}.{simple}" if package else simple)

    tf = TypeFact(
        fqn=fqn,
        kind=_type_kind_for_decl(node),
        simple_name=simple,
        file_key=file_key,
        file_path=rel,
        module_root=mod_root,
        module_label=mod_label,
    )
    if node.type == "class_declaration":
        st = get_superclass_type_node(node)
        if st:
            tf.extends_raw = type_node_to_text(st, src)
        for it in get_implements_type_list(node):
            tf.implements_raw.append(type_node_to_text(it, src))
    elif node.type == "interface_declaration":
        for it in get_extends_type_list_for_interface(node):
            tf.implements_raw.append(type_node_to_text(it, src))
    elif node.type in ("enum_declaration", "record_declaration"):
        for it in get_implements_type_list(node):
            tf.implements_raw.append(type_node_to_text(it, src))

    body = node.child_by_field_name("body")
    if body and body.type in ("class_body", "interface_body", "enum_body"):
        for ch in body.children:
            if ch.type == "field_declaration" and is_autowired_field(ch, src):
                tnode = ch.child_by_field_name("type")
                if tnode:
                    raw = extract_primary_injectable_type(tnode, src)
                    if raw:
                        tf.field_injections.append((tf.fqn, raw))

    _add_methods_to_type(node, tf, src)

    out: list[TypeFact] = [tf]
    if body and body.type in ("class_body", "interface_body", "enum_body"):
        for ch in body.children:
            if ch.type in _DECL_TYPES:
                out.extend(
                    _collect_declarations_recursive(
                        ch, src, package, file_key, rel, mod_root, mod_label, fqn
                    )
                )
    return out


def extract_file(
    path: Path,
    module_label: str,
    module_root: Path,
) -> FileFact:
    mod_s = str(module_root.resolve())
    try:
        rel = path.resolve().relative_to(module_root).as_posix()
    except ValueError:
        rel = path.name
    file_key = f"{module_label}::{rel}"

    try:
        src = path.read_bytes()
    except OSError as e:
        return FileFact(
            file_key=file_key,
            rel_path=rel,
            module_root=mod_s,
            module_label=module_label,
            package="",
            import_ctx=ImportContext(),
            error=str(e),
        )
    p = _parser()
    root = p.parse(src).root_node
    pkg = ""
    for ch in root.children:
        if ch.type == "package_declaration":
            nm = ch.child_by_field_name("name")
            if nm:
                pkg = src[nm.start_byte : nm.end_byte].decode("utf-8", errors="replace")
            else:
                for c2 in ch.children:
                    if c2.type == "scoped_identifier":
                        pkg = src[c2.start_byte : c2.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        break
            break

    ictx = collect_imports(root, src)
    types: list[TypeFact] = []
    for ch in root.children:
        if ch.type in _DECL_TYPES:
            types.extend(
                _collect_declarations_recursive(
                    ch, src, pkg, file_key, rel, mod_s, module_label, None
                )
            )
    return FileFact(
        file_key=file_key,
        rel_path=rel,
        module_root=mod_s,
        module_label=module_label,
        package=pkg,
        import_ctx=ictx,
        types=types,
    )
