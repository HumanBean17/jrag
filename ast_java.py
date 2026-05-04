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
    "CallSite",
    "FieldDecl",
    "FileImports",
    "ParamDecl",
    "MethodDecl",
    "TypeDecl",
    "JavaFileAst",
    "parse_java",
    "infer_role",
    "infer_role_for_type",
    "infer_capabilities_for_type",
    "ROLE_ANNOTATIONS",
    "ONTOLOGY_VERSION",
    "_METHOD_ANN_TO_CAPABILITY",
    "_TYPE_ANN_TO_CAPABILITY",
    "_INJECTED_TYPES_TO_CAPABILITY",
    "_SUPERTYPE_TO_CAPABILITY",
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

ONTOLOGY_VERSION = 4

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

# ---------- capability detector tables ----------

_METHOD_ANN_TO_CAPABILITY: dict[str, str] = {
    "KafkaListener":    "MESSAGE_LISTENER",
    "RabbitListener":   "MESSAGE_LISTENER",
    "JmsListener":      "MESSAGE_LISTENER",
    "SqsListener":      "MESSAGE_LISTENER",
    "EventListener":    "MESSAGE_LISTENER",
    "StreamListener":   "MESSAGE_LISTENER",
    "Scheduled":        "SCHEDULED_TASK",
    "ExceptionHandler": "EXCEPTION_HANDLER",
}

_TYPE_ANN_TO_CAPABILITY: dict[str, str] = {
    "ControllerAdvice":     "EXCEPTION_HANDLER",
    "RestControllerAdvice": "EXCEPTION_HANDLER",
}

_INJECTED_TYPES_TO_CAPABILITY: dict[str, str] = {
    "KafkaTemplate":             "MESSAGE_PRODUCER",
    "RabbitTemplate":            "MESSAGE_PRODUCER",
    "JmsTemplate":               "MESSAGE_PRODUCER",
    "StreamBridge":              "MESSAGE_PRODUCER",
    "ApplicationEventPublisher": "MESSAGE_PRODUCER",
}

_SUPERTYPE_TO_CAPABILITY: dict[str, str] = {
    "Job": "SCHEDULED_TASK",
}

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
    arguments: dict[str, str] = field(default_factory=dict)
    # Argument origin by key: "enum" | "string".
    argument_kinds: dict[str, str] = field(default_factory=dict)
    # Populated for `@CodebaseCapabilities({@CodebaseCapability("a"), ...})` — inner values.
    container_capability_values: tuple[str, ...] = field(default_factory=tuple)
    # Entry-aligned with `container_capability_values`; each value is "enum" | "string".
    container_capability_kinds: tuple[str, ...] = field(default_factory=tuple)


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
class FileImports:
    """Per-compilation-unit import maps used by call-site resolution."""

    explicit: dict[str, str] = field(default_factory=dict)  # SimpleType -> type FQN
    static_methods: dict[str, str] = field(default_factory=dict)  # simple method name -> "pkg.Type.method"
    static_wildcards: list[str] = field(default_factory=list)  # type FQNs for `import static T.*`


@dataclass
class CallSite:
    """A single static call site inside a method or constructor body."""

    caller_fqn: str  # type_fqn#signature (matches Symbol.fqn for method nodes)
    receiver_expr: str  # raw receiver text; "" for bare calls
    callee_simple: str  # method name or "<init>"
    arg_count: int  # -1 for method references (unknown)
    is_static_call: bool
    is_constructor: bool
    in_lambda: bool
    line: int
    byte: int


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
    call_sites: list[CallSite] = field(default_factory=list)
    # Ordered (name, simple_type_name) from `local_variable_declaration` in body.
    local_vars: list[tuple[str, str]] = field(default_factory=list)


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
    capabilities: list[str] = field(default_factory=list)


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
    file_imports: FileImports = field(default_factory=FileImports)


# ---------- helpers ----------


def _txt(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _annotation_name(node: Node, src: bytes) -> tuple[str, str]:
    """Extract ('simple', 'qualified-as-written') from an annotation node."""
    name_node = node.child_by_field_name("name")
    qualified = _txt(name_node, src) if name_node is not None else _txt(node, src).lstrip("@").split("(", 1)[0]
    simple = qualified.rsplit(".", 1)[-1]
    return simple, qualified


def _string_literal_value(node: Node, src: bytes) -> str | None:
    if node.type != "string_literal":
        return None
    for ch in node.children:
        if ch.type == "string_fragment":
            return _txt(ch, src)
    return None


def _annotation_value(
    node: Node, src: bytes
) -> tuple[str | None, str | None]:
    """Extract annotation value and its kind.

    Returns `(value, kind)` where kind is one of "enum" / "string".
    Enum-like expressions are normalized to the terminal constant name:
    `CodebaseRoleKind.SERVICE` -> `SERVICE`.
    """
    if node.type == "element_value" and node.named_children:
        return _annotation_value(node.named_children[0], src)

    sval = _string_literal_value(node, src)
    if sval is not None:
        return sval, "string"

    if node.type in ("identifier", "scoped_identifier", "field_access"):
        raw = _txt(node, src).strip()
        if not raw:
            return None, None
        return raw.rsplit(".", 1)[-1], "enum"

    return None, None


def _parse_annotation_argument_list(
    alist: Node, src: bytes
) -> tuple[dict[str, str], dict[str, str]]:
    """Map argument names to normalized enum/string values and value kinds."""
    out: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for ch in alist.named_children:
        if ch.type == "element_value_pair":
            key_node = ch.child_by_field_name("key")
            val_node = ch.child_by_field_name("value")
            if key_node is None:
                ids = [c for c in ch.children if c.type == "identifier"]
                key_node = ids[0] if ids else None
            if val_node is None:
                for c in reversed(ch.named_children):
                    if c is not key_node:
                        val_node = c
                        break
            if key_node is None or val_node is None:
                continue
            key = _txt(key_node, src)
            val, kind = _annotation_value(val_node, src)
            if val is not None and kind is not None:
                out[key] = val
                kinds[key] = kind
        else:
            v, kind = _annotation_value(ch, src)
            if v is not None and kind is not None and "value" not in out:
                out["value"] = v
                kinds["value"] = kind
    return out, kinds


def _codebase_capability_values_from_array(
    ann_node: Node, src: bytes
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    found: list[str] = []
    kinds: list[str] = []

    def visit(n: Node) -> None:
        if n.type == "annotation":
            name_node = n.child_by_field_name("name")
            n_simple = _txt(name_node, src).rsplit(".", 1)[-1] if name_node is not None else ""
            if n_simple == "CodebaseCapability":
                for c in n.children:
                    if c.type == "annotation_argument_list":
                        m, mk = _parse_annotation_argument_list(c, src)
                        v = m.get("value")
                        k = mk.get("value")
                        if v is not None and k is not None:
                            found.append(v)
                            kinds.append(k)
        for c in n.children:
            visit(c)

    visit(ann_node)
    return tuple(found), tuple(kinds)


def _parse_annotation_ref_node(node: Node, src: bytes) -> AnnotationRef:
    """Build `AnnotationRef` for a `marker_annotation` or `annotation` node."""
    simple, qualified = _annotation_name(node, src)
    t = node.type
    if t == "marker_annotation":
        return AnnotationRef(name=simple, qualified=qualified, arguments={})

    args: dict[str, str] = {}
    arg_kinds: dict[str, str] = {}
    container: tuple[str, ...] = ()
    container_kinds: tuple[str, ...] = ()
    alist = node.child_by_field_name("arguments")
    if alist is None:
        for ch in node.children:
            if ch.type == "annotation_argument_list":
                alist = ch
                break
    if alist is not None:
        args, arg_kinds = _parse_annotation_argument_list(alist, src)
    if simple == "CodebaseCapabilities" and alist is not None:
        container, container_kinds = _codebase_capability_values_from_array(node, src)
    return AnnotationRef(
        name=simple,
        qualified=qualified,
        arguments=args,
        argument_kinds=arg_kinds,
        container_capability_values=container,
        container_capability_kinds=container_kinds,
    )


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
            anns.append(_parse_annotation_ref_node(child, src))
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


def _import_declaration_is_static(node: Node, src: bytes) -> bool:
    for c in node.children:
        if c.type == "static" and _txt(c, src) == "static":
            return True
    return False


def _arg_list_count(arg_list: Node | None) -> int:
    if arg_list is None:
        return 0
    return len(arg_list.named_children)


def _infer_static_method_invocation(obj: Node | None, src: bytes) -> bool:
    """Heuristic: ClassName.method() vs instance.method()."""
    if obj is None:
        return False
    if obj.type in ("type_identifier", "scoped_type_identifier"):
        return True
    if obj.type == "this" or obj.type == "super":
        return False
    if obj.type == "identifier":
        name = _txt(obj, src)
        return len(name) > 0 and name[0].isupper()
    return False


def _collect_local_vars(body: Node, src: bytes) -> list[tuple[str, str]]:
    """Declaration order: (variable name, head simple type name)."""
    out: list[tuple[str, str]] = []
    if body is None:
        return out

    def visit(n: Node) -> None:
        if n.type == "local_variable_declaration":
            type_node = n.child_by_field_name("type")
            if type_node is None:
                return
            t_simple = _strip_type_to_simple(type_node, src)
            for ch in n.named_children:
                if ch.type != "variable_declarator":
                    continue
                name_node = ch.child_by_field_name("name")
                if name_node is not None:
                    out.append((_txt(name_node, src), t_simple))
            return
        for c in n.children:
            visit(c)

    visit(body)
    return out


def _collect_call_sites(
    body: Node,
    src: bytes,
    *,
    caller_fqn: str,
    in_lambda: bool,
) -> list[CallSite]:
    """Walk a block body and collect CallSite records (attributed to caller_fqn)."""
    out: list[CallSite] = []

    def add_site(
        *,
        receiver_expr: str,
        callee_simple: str,
        arg_count: int,
        is_static_call: bool,
        is_constructor: bool,
        line: int,
        byte: int,
        lam: bool,
    ) -> None:
        out.append(
            CallSite(
                caller_fqn=caller_fqn,
                receiver_expr=receiver_expr,
                callee_simple=callee_simple,
                arg_count=arg_count,
                is_static_call=is_static_call,
                is_constructor=is_constructor,
                in_lambda=lam,
                line=line,
                byte=byte,
            )
        )

    def visit(n: Node, lam: bool) -> None:
        t = n.type
        if t == "lambda_expression":
            body_node = n.child_by_field_name("body")
            if body_node is not None:
                visit(body_node, True)
            return
        if t == "object_creation_expression":
            type_node = n.child_by_field_name("type")
            args = n.child_by_field_name("arguments")
            if type_node is not None:
                recv = _txt(type_node, src)
                line = n.start_point[0] + 1
                add_site(
                    receiver_expr=recv,
                    callee_simple="<init>",
                    arg_count=_arg_list_count(args),
                    is_static_call=False,
                    is_constructor=True,
                    line=line,
                    byte=n.start_byte,
                    lam=lam,
                )
            for ch in n.named_children:
                if ch.type == "class_body":
                    visit(ch, True)
                else:
                    visit(ch, lam)
            return
        if t == "method_invocation":
            obj = n.child_by_field_name("object")
            name_node = n.child_by_field_name("name")
            callee = _txt(name_node, src) if name_node is not None else ""
            args = n.child_by_field_name("arguments")
            argc = _arg_list_count(args)
            line = n.start_point[0] + 1
            if obj is None:
                recv = ""
                static_call = False
            else:
                recv = _txt(obj, src)
                static_call = _infer_static_method_invocation(obj, src)
            add_site(
                receiver_expr=recv,
                callee_simple=callee,
                arg_count=argc,
                is_static_call=static_call,
                is_constructor=False,
                line=line,
                byte=n.start_byte,
                lam=lam,
            )
            for ch in n.children:
                visit(ch, lam)
            return
        if t == "method_reference":
            parts = [c for c in n.children if c.type != "::"]
            if not parts:
                for ch in n.children:
                    visit(ch, lam)
                return
            name_node = parts[-1]
            if name_node.type != "identifier":
                for ch in n.children:
                    visit(ch, lam)
                return
            name_id = _txt(name_node, src)
            qual = parts[0] if len(parts) >= 2 else None
            recv = _txt(qual, src) if qual is not None else ""
            chained = qual is not None and qual.type == "method_invocation"
            add_site(
                receiver_expr=recv,
                callee_simple=name_id,
                arg_count=-1,
                is_static_call=False,
                is_constructor=False,
                line=n.start_point[0] + 1,
                byte=n.start_byte,
                lam=lam or chained,
            )
            for ch in n.children:
                visit(ch, lam)
            return
        if t == "explicit_constructor_invocation":
            is_super = any(c.type == "super" for c in n.children)
            recv = "super" if is_super else "this"
            args = n.child_by_field_name("arguments")
            if args is None:
                for c in n.named_children:
                    if c.type == "argument_list":
                        args = c
                        break
            add_site(
                receiver_expr=recv,
                callee_simple="<init>",
                arg_count=_arg_list_count(args),
                is_static_call=False,
                is_constructor=True,
                line=n.start_point[0] + 1,
                byte=n.start_byte,
                lam=lam,
            )
            return
        for ch in n.children:
            visit(ch, lam)

    visit(body, in_lambda)
    return out


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


def _parse_method(node: Node, src: bytes, *, is_constructor: bool, type_fqn: str) -> MethodDecl:
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
    m = MethodDecl(
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
    caller_fqn = f"{type_fqn}#{signature}"
    body = node.child_by_field_name("body")
    if body is not None:
        m.local_vars = _collect_local_vars(body, src)
        sites = _collect_call_sites(body, src, caller_fqn=caller_fqn, in_lambda=False)
        if is_constructor:
            had_explicit = any(
                s.callee_simple == "<init>" and s.receiver_expr in ("this", "super") for s in sites
            )
            if not had_explicit:
                sites.append(
                    CallSite(
                        caller_fqn=caller_fqn,
                        receiver_expr="super",
                        callee_simple="<init>",
                        arg_count=0,
                        is_static_call=False,
                        is_constructor=True,
                        in_lambda=False,
                        line=m.start_line,
                        byte=m.start_byte,
                    )
                )
        m.call_sites = sites
    return m


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
                methods.append(_parse_method(ch, src, is_constructor=False, type_fqn=fqn))
            elif ch.type == "constructor_declaration":
                methods.append(_parse_method(ch, src, is_constructor=True, type_fqn=fqn))
            elif ch.type in _TYPE_KINDS:
                nested.append(_parse_type(ch, src, package=package, outer_fqn=fqn))

    # Synthesize a default no-arg constructor when:
    #   - the type is a class or enum (not interface/annotation/record),
    #   - no explicit constructor was parsed, AND
    #   - no Lombok annotation that generates a constructor is present
    #     (@RequiredArgsConstructor / @AllArgsConstructor would synthesize an
    #     args-bearing ctor; adding a no-arg one here would mis-resolve callers).
    ann_names_set = {a.name for a in anns}
    if (
        kind in ("class", "enum")
        and not any(m.is_constructor for m in methods)
        and not (_LOMBOK_RAC & ann_names_set)
    ):
        default_ctor_sig = "<init>()"
        default_ctor = MethodDecl(
            name="<init>",
            return_type="",
            is_constructor=True,
            parameters=[],
            modifiers=[],
            annotations=[],
            signature=default_ctor_sig,
            start_byte=node.start_byte,
            end_byte=node.start_byte,
            start_line=node.start_point[0] + 1,
            end_line=node.start_point[0] + 1,
            call_sites=[],
            local_vars=[],
        )
        methods.append(default_ctor)

    type_decl = TypeDecl(
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
    type_decl.capabilities = infer_capabilities_for_type(type_decl)
    return type_decl


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
        file_imports=FileImports(),
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
    static_methods: dict[str, str] = {}
    static_wildcards: list[str] = []
    top_types: list[TypeDecl] = []

    for child in root.named_children:
        t = child.type
        if t == "package_declaration":
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    package = _txt(c, src)
                    break
        elif t == "import_declaration":
            is_static = _import_declaration_is_static(child, src)
            has_wild = any(c.type == "asterisk" for c in child.children)
            ident_node = None
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    ident_node = c
                    break
            if ident_node is None:
                continue
            ident = _txt(ident_node, src)
            if is_static:
                if has_wild:
                    static_wildcards.append(ident)
                    imports.append(f"import static {ident}.*")
                else:
                    simple = ident.rsplit(".", 1)[-1]
                    static_methods[simple] = ident
                    imports.append(f"import static {ident}")
                continue
            if has_wild:
                wildcard_imports.append(ident)
                imports.append(f"{ident}.*")
            else:
                imports.append(ident)
                simple = ident.rsplit(".", 1)[-1]
                explicit_imports[simple] = ident

    file_imports = FileImports(
        explicit=explicit_imports,
        static_methods=static_methods,
        static_wildcards=static_wildcards,
    )
    for child in root.named_children:
        if child.type in _TYPE_KINDS:
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
        file_imports=file_imports,
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


def infer_capabilities_for_type(type_decl: "TypeDecl") -> list[str]:
    """Aggregate type-level capabilities. Stable, sorted, deduplicated.

    Pure function: derives capabilities from the parsed AST only. Does
    not consult external configuration; brownfield overrides are merged
    later in `graph_enrich.py` so this stays free of I/O.
    """
    caps: set[str] = set()

    for ann in type_decl.annotations:
        cap = _TYPE_ANN_TO_CAPABILITY.get(ann.name)
        if cap:
            caps.add(cap)

    for method in type_decl.methods:
        for ann in method.annotations:
            cap = _METHOD_ANN_TO_CAPABILITY.get(ann.name)
            if cap:
                caps.add(cap)

    for fld in type_decl.fields:
        cap = _INJECTED_TYPES_TO_CAPABILITY.get(fld.type_name)
        if cap:
            caps.add(cap)
    for method in type_decl.methods:
        if method.is_constructor:
            for p in method.parameters:
                cap = _INJECTED_TYPES_TO_CAPABILITY.get(p.type_name)
                if cap:
                    caps.add(cap)

    for sup in (*type_decl.extends, *type_decl.implements):
        cap = _SUPERTYPE_TO_CAPABILITY.get(sup)
        if cap:
            caps.add(cap)

    return sorted(caps)


def injection_annotation_names() -> frozenset[str]:
    return _INJECT_FIELD_ANNOTATIONS


def lombok_required_args_annotations() -> frozenset[str]:
    return _LOMBOK_RAC
