"""Deterministic Java AST extraction on top of tree-sitter.

Produces a typed, stable view of a single .java compilation unit:
package, imports, and a tree of TypeDecl (class/interface/enum/record/annotation)
with their annotations, fields, methods, and nested types.

The output is deliberately language-model friendly (simple names, no tree-sitter
Nodes leak through) so downstream graph / chunk-enrichment code can stay pure
Python with no tree-sitter dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

import tree_sitter_java as _ts_java
from tree_sitter import Language, Node, Parser

__all__ = [
    "AnnotationRef",
    "FieldDecl",
    "ParamDecl",
    "MethodDecl",
    "TypeDecl",
    "JavaFileAst",
    "parse_java",
    "infer_role",
    "infer_role_for_type",
    "ROLE_ANNOTATIONS",
    "ONTOLOGY_VERSION",
]

# Name suffixes that strongly indicate a passive data-carrier type when the
# type is not annotated as a Spring/JPA component. Kept conservative on
# purpose: a single clear suffix match is enough, but only when role
# inference would otherwise return OTHER (so @Service FooRequest stays a
# SERVICE). Checked case-sensitively against the simple type name.
_DTO_NAME_SUFFIXES: tuple[str, ...] = (
    "Dto", "DTO",
    "Request", "Response",
    "Payload", "Model",
    "Event", "Message",
    "Body", "Form",
    "Command", "Query",
    "Record", "View",
)

# Lombok value / builder annotations typical of DTO-style types. Presence of
# any one of these promotes an otherwise role-less type to DTO.
_DTO_LOMBOK_ANNOTATIONS: frozenset[str] = frozenset({
    "Data", "Value", "Builder",
    "Getter", "Setter",
    "EqualsAndHashCode", "ToString",
})

ONTOLOGY_VERSION = 1

ROLE_ANNOTATIONS: dict[str, str] = {
    # Spring Web
    "RestController": "CONTROLLER",
    "Controller": "CONTROLLER",
    # Spring stereotypes
    "Service": "SERVICE",
    "Repository": "REPOSITORY",
    "Component": "COMPONENT",
    "Configuration": "CONFIG",
    # Persistence
    "Entity": "ENTITY",
    "MappedSuperclass": "ENTITY",
    "Embeddable": "ENTITY",
    # Remoting / messaging
    "FeignClient": "FEIGN_CLIENT",
    # Mappers
    "Mapper": "MAPPER",
}

_INJECT_FIELD_ANNOTATIONS = frozenset({"Autowired", "Inject", "Resource"})
_LOMBOK_RAC = frozenset({"RequiredArgsConstructor", "AllArgsConstructor"})
_TYPE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}


@lru_cache(maxsize=1)
def _parser() -> Parser:
    lang = Language(_ts_java.language())
    return Parser(lang)


# ---------- dataclasses ----------


@dataclass
class AnnotationRef:
    name: str  # simple (last segment); e.g. "RestController"
    qualified: str  # raw source text, e.g. "org.springframework.web.bind.annotation.RestController"


@dataclass
class FieldDecl:
    name: str
    type_name: str  # simple name, generics + arrays stripped
    type_raw: str  # original text
    modifiers: list[str] = field(default_factory=list)
    annotations: list[AnnotationRef] = field(default_factory=list)
    start_byte: int = 0
    end_byte: int = 0
    start_line: int = 0
    end_line: int = 0


@dataclass
class ParamDecl:
    name: str
    type_name: str
    type_raw: str
    annotations: list[AnnotationRef] = field(default_factory=list)


@dataclass
class MethodDecl:
    name: str
    return_type: str  # simple name; "" for constructors / void kept as "void"
    is_constructor: bool
    parameters: list[ParamDecl] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    annotations: list[AnnotationRef] = field(default_factory=list)
    signature: str = ""  # "name(T1,T2)"
    start_byte: int = 0
    end_byte: int = 0
    start_line: int = 0
    end_line: int = 0


@dataclass
class TypeDecl:
    name: str
    kind: str
    fqn: str
    modifiers: list[str] = field(default_factory=list)
    annotations: list[AnnotationRef] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)  # simple names
    implements: list[str] = field(default_factory=list)
    fields: list[FieldDecl] = field(default_factory=list)
    methods: list[MethodDecl] = field(default_factory=list)
    nested: list["TypeDecl"] = field(default_factory=list)
    start_byte: int = 0
    end_byte: int = 0
    start_line: int = 0
    end_line: int = 0
    outer_fqn: str | None = None  # None for top-level


@dataclass
class JavaFileAst:
    package: str
    imports: list[str]  # raw, as written (may include ".*" suffix)
    wildcard_imports: list[str]  # e.g. "java.util"
    explicit_imports: dict[str, str]  # "List" -> "java.util.List"
    top_level_types: list[TypeDecl]
    all_types: list[TypeDecl]  # flat, includes nested
    parse_error: bool = False
    source_bytes: int = 0


# ---------- helpers ----------


def _txt(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _annotation_name(node: Node, src: bytes) -> tuple[str, str]:
    """Extract ('simple', 'qualified-as-written') from an annotation node."""
    name_node = node.child_by_field_name("name")
    qualified = _txt(name_node, src) if name_node is not None else _txt(node, src).lstrip("@").split("(", 1)[0]
    simple = qualified.rsplit(".", 1)[-1]
    return simple, qualified


_MODIFIER_KEYWORDS = frozenset({
    "public",
    "private",
    "protected",
    "static",
    "final",
    "abstract",
    "default",
    "synchronized",
    "native",
    "transient",
    "volatile",
    "strictfp",
    "sealed",
    "non-sealed",
})


def _find_modifiers_child(parent: Node) -> Node | None:
    """tree-sitter-java exposes `modifiers` as an unnamed-field child node."""
    for ch in parent.children:
        if ch.type == "modifiers":
            return ch
    return None


def _collect_annotations_and_modifiers(
    parent: Node, src: bytes
) -> tuple[list[str], list[AnnotationRef]]:
    """Extract modifiers + annotations from the `modifiers` sibling of `parent`."""
    mods_node = _find_modifiers_child(parent)
    if mods_node is None:
        return [], []
    mods: list[str] = []
    anns: list[AnnotationRef] = []
    for child in mods_node.children:
        t = child.type
        if t in ("marker_annotation", "annotation"):
            simple, qualified = _annotation_name(child, src)
            anns.append(AnnotationRef(name=simple, qualified=qualified))
        elif t in _MODIFIER_KEYWORDS:
            mods.append(t)
    return mods, anns


def _strip_type_to_simple(type_node: Node, src: bytes) -> str:
    """Reduce any type expression to its head simple name.

    Examples:
        List<String>            -> List
        java.util.List<Foo>     -> List
        Map<String,Integer>[][] -> Map
        String[]                -> String
        void                    -> void
        int                     -> int
    """
    t = type_node.type
    if t == "generic_type":
        # first child is the name part (type_identifier | scoped_type_identifier)
        for ch in type_node.children:
            if ch.type in ("type_identifier", "scoped_type_identifier"):
                return _strip_type_to_simple(ch, src)
        return _txt(type_node, src).split("<", 1)[0].rsplit(".", 1)[-1]
    if t == "array_type":
        elem = type_node.child_by_field_name("element") or (type_node.named_children[0] if type_node.named_children else None)
        if elem is not None:
            return _strip_type_to_simple(elem, src)
        return _txt(type_node, src).split("[", 1)[0]
    if t == "scoped_type_identifier":
        return _txt(type_node, src).rsplit(".", 1)[-1]
    if t == "type_identifier":
        return _txt(type_node, src)
    # primitive / void / anything else: return raw
    return _txt(type_node, src)


def _collect_type_list(node: Node, src: bytes) -> list[str]:
    """Turn an `interface_type_list` / `type_list` / single type into simple names."""
    out: list[str] = []
    if node.type in ("type_list", "interface_type_list", "super_interfaces", "extends_interfaces"):
        for ch in node.named_children:
            if ch.type == "type_list" or ch.type == "interface_type_list":
                out.extend(_collect_type_list(ch, src))
            else:
                out.append(_strip_type_to_simple(ch, src))
        return out
    return [_strip_type_to_simple(node, src)]


def _extends_of(type_node: Node, src: bytes) -> list[str]:
    out: list[str] = []
    # class: superclass field; interface: extends_interfaces field
    sc = type_node.child_by_field_name("superclass")
    if sc is not None:
        # `superclass` node has children like `extends` + type
        for ch in sc.named_children:
            out.append(_strip_type_to_simple(ch, src))
    ei = type_node.child_by_field_name("interfaces")
    # for interface declarations, the extends clause uses field "extends" via `extends_interfaces`
    if ei is None:
        for ch in type_node.children:
            if ch.type in ("extends_interfaces",):
                for sub in ch.named_children:
                    if sub.type in ("type_list", "interface_type_list"):
                        out.extend(_collect_type_list(sub, src))
                    else:
                        out.append(_strip_type_to_simple(sub, src))
    return out


def _implements_of(type_node: Node, src: bytes) -> list[str]:
    out: list[str] = []
    si = type_node.child_by_field_name("interfaces")
    if si is not None:
        for ch in si.named_children:
            if ch.type in ("type_list", "interface_type_list"):
                out.extend(_collect_type_list(ch, src))
            else:
                out.append(_strip_type_to_simple(ch, src))
        return out
    # fallback for grammars exposing super_interfaces unnamed
    for ch in type_node.children:
        if ch.type == "super_interfaces":
            for sub in ch.named_children:
                if sub.type in ("type_list", "interface_type_list"):
                    out.extend(_collect_type_list(sub, src))
                else:
                    out.append(_strip_type_to_simple(sub, src))
    return out


def _iter_body_members(body: Node) -> Iterable[Node]:
    if body is None:
        return []
    return [c for c in body.named_children]


def _parse_params(formal: Node | None, src: bytes) -> list[ParamDecl]:
    if formal is None:
        return []
    params: list[ParamDecl] = []
    for ch in formal.named_children:
        if ch.type not in ("formal_parameter", "spread_parameter"):
            continue
        _, p_anns = _collect_annotations_and_modifiers(ch, src)
        type_node = ch.child_by_field_name("type")
        name_node = ch.child_by_field_name("name")
        if type_node is None or name_node is None:
            continue
        params.append(
            ParamDecl(
                name=_txt(name_node, src),
                type_name=_strip_type_to_simple(type_node, src),
                type_raw=_txt(type_node, src),
                annotations=p_anns,
            )
        )
    return params


def _parse_method(node: Node, src: bytes, *, is_constructor: bool) -> MethodDecl:
    mods, anns = _collect_annotations_and_modifiers(node, src)
    name_node = node.child_by_field_name("name")
    name = _txt(name_node, src) if name_node is not None else ("<init>" if is_constructor else "<method>")
    ret_node = node.child_by_field_name("type")
    if is_constructor:
        return_type = ""
    else:
        return_type = _strip_type_to_simple(ret_node, src) if ret_node is not None else ""
    params = _parse_params(node.child_by_field_name("parameters"), src)
    sig_params = ",".join(p.type_name for p in params)
    signature = f"{name}({sig_params})"
    return MethodDecl(
        name=name,
        return_type=return_type,
        is_constructor=is_constructor,
        parameters=params,
        modifiers=mods,
        annotations=anns,
        signature=signature,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )


def _parse_field(node: Node, src: bytes) -> list[FieldDecl]:
    """A single `field_declaration` may declare multiple variables."""
    mods, anns = _collect_annotations_and_modifiers(node, src)
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return []
    type_simple = _strip_type_to_simple(type_node, src)
    type_raw = _txt(type_node, src)
    out: list[FieldDecl] = []
    for ch in node.named_children:
        if ch.type != "variable_declarator":
            continue
        name_node = ch.child_by_field_name("name")
        if name_node is None:
            continue
        out.append(
            FieldDecl(
                name=_txt(name_node, src),
                type_name=type_simple,
                type_raw=type_raw,
                modifiers=list(mods),
                annotations=list(anns),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
        )
    return out


def _parse_type(node: Node, src: bytes, *, package: str, outer_fqn: str | None) -> TypeDecl:
    kind = _TYPE_KINDS[node.type]
    name_node = node.child_by_field_name("name")
    name = _txt(name_node, src) if name_node is not None else "<anon>"
    if outer_fqn:
        fqn = f"{outer_fqn}.{name}"
    elif package:
        fqn = f"{package}.{name}"
    else:
        fqn = name

    mods, anns = _collect_annotations_and_modifiers(node, src)

    extends = _extends_of(node, src)
    implements = _implements_of(node, src)

    body = node.child_by_field_name("body")
    fields: list[FieldDecl] = []
    methods: list[MethodDecl] = []
    nested: list[TypeDecl] = []
    if body is not None:
        for ch in _iter_body_members(body):
            if ch.type == "field_declaration":
                fields.extend(_parse_field(ch, src))
            elif ch.type == "method_declaration":
                methods.append(_parse_method(ch, src, is_constructor=False))
            elif ch.type == "constructor_declaration":
                methods.append(_parse_method(ch, src, is_constructor=True))
            elif ch.type in _TYPE_KINDS:
                nested.append(_parse_type(ch, src, package=package, outer_fqn=fqn))

    return TypeDecl(
        name=name,
        kind=kind,
        fqn=fqn,
        modifiers=mods,
        annotations=anns,
        extends=extends,
        implements=implements,
        fields=fields,
        methods=methods,
        nested=nested,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        outer_fqn=outer_fqn,
    )


def _flatten(types: list[TypeDecl]) -> list[TypeDecl]:
    out: list[TypeDecl] = []
    stack = list(types)
    while stack:
        t = stack.pop()
        out.append(t)
        stack.extend(t.nested)
    return out


# ---------- public API ----------


def parse_java(source: bytes | str) -> JavaFileAst:
    """Parse a Java file into a JavaFileAst. Never raises on invalid source."""
    if isinstance(source, str):
        src = source.encode("utf-8", errors="replace")
    else:
        src = source

    empty = JavaFileAst(
        package="",
        imports=[],
        wildcard_imports=[],
        explicit_imports={},
        top_level_types=[],
        all_types=[],
        parse_error=False,
        source_bytes=len(src),
    )

    if not src:
        return empty

    try:
        tree = _parser().parse(src)
    except Exception:
        empty.parse_error = True
        return empty

    root = tree.root_node
    package = ""
    imports: list[str] = []
    wildcard_imports: list[str] = []
    explicit_imports: dict[str, str] = {}
    top_types: list[TypeDecl] = []

    for child in root.named_children:
        t = child.type
        if t == "package_declaration":
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    package = _txt(c, src)
                    break
        elif t == "import_declaration":
            has_wild = any(c.type == "asterisk" for c in child.children)
            ident_node = None
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    ident_node = c
                    break
            if ident_node is None:
                continue
            ident = _txt(ident_node, src)
            if has_wild:
                wildcard_imports.append(ident)
                imports.append(f"{ident}.*")
            else:
                imports.append(ident)
                simple = ident.rsplit(".", 1)[-1]
                explicit_imports[simple] = ident
        elif t in _TYPE_KINDS:
            top_types.append(_parse_type(child, src, package=package, outer_fqn=None))

    all_types = _flatten(top_types)
    return JavaFileAst(
        package=package,
        imports=imports,
        wildcard_imports=wildcard_imports,
        explicit_imports=explicit_imports,
        top_level_types=top_types,
        all_types=all_types,
        parse_error=root.has_error,
        source_bytes=len(src),
    )


def infer_role(annotation_names: Iterable[str]) -> str:
    """Map a set of simple annotation names to a single role. First hit wins."""
    for ann in annotation_names:
        role = ROLE_ANNOTATIONS.get(ann)
        if role:
            return role
    return "OTHER"


def infer_role_for_type(type_decl: "TypeDecl") -> str:
    """Role inference that also detects DTO-like passive data carriers.

    Applied only when annotation-based inference yields OTHER, so an
    explicitly-stereotyped class (e.g. @Service FooRequest) keeps its role.
    A type is considered DTO when *any* of the following hold:

      * kind is `record` (Java records are value carriers by definition);
      * a Lombok value/getter/setter annotation is present (`@Data`, etc.);
      * the simple name ends with a known DTO suffix (`Dto`, `Request`, ...).

    Used to down-rank DTOs in behavioural search; schema-focused queries can
    still fetch them via explicit `role=DTO` or by turning the weight off.
    """
    ann_names = [a.name for a in type_decl.annotations]
    base = infer_role(ann_names)
    if base != "OTHER":
        return base

    if type_decl.kind == "record":
        return "DTO"

    ann_set = set(ann_names)
    if ann_set & _DTO_LOMBOK_ANNOTATIONS:
        return "DTO"

    name = type_decl.name or ""
    for suffix in _DTO_NAME_SUFFIXES:
        if name.endswith(suffix) and name != suffix:
            return "DTO"

    return "OTHER"


def injection_annotation_names() -> frozenset[str]:
    return _INJECT_FIELD_ANNOTATIONS


def lombok_required_args_annotations() -> frozenset[str]:
    return _LOMBOK_RAC
