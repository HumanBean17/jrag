"""Deterministic Java AST extraction on top of tree-sitter.

Produces a typed, stable view of a single .java compilation unit:
package, imports, and a tree of TypeDecl (class/interface/enum/record/annotation)
with their annotations, fields, methods, and nested types. Anonymous classes
(`new T() { … }`) become synthetic nested TypeDecl rows (`<anon:startByte>`) so
their method bodies own call-site lists (see `propose/completed/CALL-GRAPH-PROPOSE.md` §4.1).

The output is deliberately language-model friendly (simple names, no tree-sitter
Nodes leak through) so downstream graph / chunk-enrichment code can stay pure
Python with no tree-sitter dependency.
"""
from __future__ import annotations

import posixpath
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

import tree_sitter_java as _ts_java

from brownfield_events import (
    emit_brownfield_exclusivity_shadowing,
    emit_brownfield_method_string_literal,
)
from tree_sitter import Language, Node, Parser

__all__ = [
    "AnnotationRef",
    "CallSite",
    "FieldDecl",
    "FileImports",
    "ParamDecl",
    "MethodDecl",
    "OutgoingCallDecl",
    "RouteDecl",
    "ROUTE_META_ANNOTATION_NAMES",
    "CODEBASE_ROUTE_ANNOTATIONS",
    "CODEBASE_HTTP_CLIENT_ANNOTATIONS",
    "CODEBASE_PRODUCER_ANNOTATIONS",
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

# Phase 5: HTTP_CALLS + ASYNC_CALLS (B2b); Phase 6: cross-service resolution mode on GraphMeta;
# Phase 7: FEIGN_CLIENT role -> CLIENT + HTTP_CLIENT capability vocabulary cleanup;
# Phase 8: first-class Client node + DECLARES_CLIENT relation, separating outbound declarations from Route.
# Phase 9: `@CodebaseAsyncRoute` replaces same-method built-in `@KafkaListener` routes in graph composition.
# Phase 10: `@CodebaseHttpClient` rename + `CodebaseHttpMethod` enum; inbound HTTP layer-C replaces built-in rows.
# Phase 11: `EDGE_SCHEMA` in `java_ontology.py` (canonical edge navigation schema; v14 re-index).
# Phase 12: CALLS `callee_declaring_role`, supertype-walk dedup, pass3 unresolved counters (v15 re-index).
# Bumps whenever extraction / enrichment semantics change.
ONTOLOGY_VERSION = 17

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
    "FeignClient": "CLIENT",
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
    "FeignClient":          "HTTP_CLIENT",
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

_ROUTE_HTTP_MAPPING_NAMES = frozenset({
    "RequestMapping",
    "GetMapping",
    "PostMapping",
    "PutMapping",
    "DeleteMapping",
    "PatchMapping",
})

# Seeds for `collect_annotation_meta_chain` so custom @interface meta-annotations
# (e.g. @AcmeGet meta-@GetMapping) resolve in Layer A (see graph_enrich._meta_builtins).
ROUTE_META_ANNOTATION_NAMES: frozenset[str] = _ROUTE_HTTP_MAPPING_NAMES | frozenset({
    "KafkaListener",
    "RabbitListener",
    "JmsListener",
    "StreamListener",
})

CODEBASE_ROUTE_ANNOTATIONS: frozenset[str] = frozenset({
    "CodebaseHttpRoute",
    "CodebaseHttpRoutes",
    "CodebaseAsyncRoute",
    "CodebaseAsyncRoutes",
})
CODEBASE_HTTP_CLIENT_ANNOTATIONS: frozenset[str] = frozenset(
    {"CodebaseHttpClient", "CodebaseHttpClients"}
)

# Framework annotations bypassed when `@CodebaseHttpRoute` / `@CodebaseHttpClient` wins (verbose INFO).
_BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS: frozenset[str] = (
    _ROUTE_HTTP_MAPPING_NAMES
    | frozenset({
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    })
)
CODEBASE_PRODUCER_ANNOTATIONS: frozenset[str] = frozenset({"CodebaseProducer", "CodebaseProducers"})

_ROUTE_ASYNC_METHOD_NAMES = frozenset({
    "KafkaListener",
    "RabbitListener",
    "JmsListener",
    "StreamListener",
})

_TYPE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}

# For `new Super() { }` when `Super` is not declared in the same compilation unit:
# treat these simple names as interfaces (implements) vs classes (extends).
_ANON_SUPER_AS_INTERFACE: frozenset[str] = frozenset({
    "Runnable", "Callable", "Comparable", "Iterable", "Iterator", "AutoCloseable",
    "Closeable", "Flushable", "Readable", "Appendable", "Cloneable", "Serializable",
    "Externalizable", "InvocationHandler", "ThreadFactory", "PrivilegedAction",
    "PrivilegedExceptionAction", "Comparator", "Consumer", "BiConsumer", "Supplier",
    "Function", "BiFunction", "UnaryOperator", "BinaryOperator", "Predicate",
    "BiPredicate", "IntConsumer", "LongConsumer", "DoubleConsumer", "IntFunction",
    "LongFunction", "DoubleFunction", "IntPredicate", "LongPredicate", "DoublePredicate",
    "IntSupplier", "LongSupplier", "DoubleSupplier", "ToIntFunction", "ToLongFunction",
    "ToDoubleFunction", "Stream", "BaseStream", "Collector", "Observer", "Observable",
    "List", "Set", "Map", "Queue", "Deque", "Collection", "EventListener",
    "ActionListener", "MouseListener", "KeyListener", "WindowListener", "RowMapper",
    "ResultSetExtractor", "PreparedStatementCreator", "CallableStatementCallback",
})


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
    chained_method_reference: bool = False  # true for ``expr::name`` where expr is a call chain


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
    routes: list["RouteDecl"] = field(default_factory=list)
    outgoing_calls: list["OutgoingCallDecl"] = field(default_factory=list)


@dataclass
class RouteDecl:
    """Extracted route declaration anchored on a method (B2a).

    `method_fqn` matches graph Symbol.fqn (`type.pkg.Type#name(T1,T2)`).
    """

    method_fqn: str
    method_sig: str
    kind: str
    framework: str
    http_method: str
    path: str
    topic: str
    broker: str
    feign_name: str
    feign_url: str
    resolution_strategy: str
    confidence: float
    resolved: bool
    filename: str
    start_line: int
    end_line: int
    # brownfield / B2a composition (graph_enrich.resolve_routes_for_method); not a graph column.
    route_source_layer: str = "builtin"


@dataclass
class OutgoingCallDecl:
    method_fqn: str
    method_sig: str
    client_kind: str
    channel: str
    feign_target_name: str
    feign_target_url: str
    path_template_call: str
    method_call: str
    topic_call: str
    broker_call: str
    raw_uri: str
    raw_topic: str
    resolution_strategy: str
    confidence_base: float
    resolved: bool
    filename: str
    start_line: int
    end_line: int


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
    routes_skipped_unresolved: int = 0


@dataclass
class _ParseCtx:
    routes_skipped_unresolved: int = 0
    verbose: bool = False


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


def _pre_scan_declared_type_kinds(root: Node, src: bytes) -> dict[str, str]:
    """Map simple type name -> kind for declarations in this CU (for anonymous super)."""
    out: dict[str, str] = {}

    def visit(n: Node) -> None:
        t = n.type
        if t in _TYPE_KINDS:
            nn = n.child_by_field_name("name")
            if nn is not None:
                nm = _txt(nn, src)
                if nm and nm != "<anon>":
                    out[nm] = _TYPE_KINDS[t]
        for c in n.children:
            visit(c)

    visit(root)
    return out


def _anonymous_extends_implements(
    super_simple: str, kind_by_simple: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Java: anonymous extends class X, or extends Object + implements I."""
    k = kind_by_simple.get(super_simple)
    if k == "interface":
        return [], [super_simple]
    if k in ("class", "enum", "record"):
        return [super_simple], []
    if super_simple in _ANON_SUPER_AS_INTERFACE:
        return [], [super_simple]
    return [super_simple], []


def _parse_type_body_into_decl(
    body: Node,
    src: bytes,
    *,
    package: str,
    fqn: str,
    kind: str,
    extends: list[str],
    implements: list[str],
    modifiers: list[str],
    annotations: list[AnnotationRef],
    kind_by_simple: dict[str, str],
    start_byte: int,
    end_byte: int,
    start_line: int,
    end_line: int,
    outer_fqn: str | None,
    enclosing_type_node: Node | None,
    file_rel: str,
    ctx: _ParseCtx,
) -> TypeDecl:
    """Shared member parsing for named types and synthetic anonymous classes."""
    fields: list[FieldDecl] = []
    methods: list[MethodDecl] = []
    nested: list[TypeDecl] = []
    anon_nested: list[TypeDecl] = []
    for ch in _iter_body_members(body):
        if ch.type == "field_declaration":
            fields.extend(_parse_field(ch, src))
        elif ch.type == "method_declaration":
            m, anons = _parse_method(
                ch, src, is_constructor=False, type_fqn=fqn,
                package=package, kind_by_simple=kind_by_simple,
                ctx=ctx,
                enclosing_type_node=enclosing_type_node,
                type_kind=kind,
                type_anns=annotations,
                file_rel=file_rel,
            )
            methods.append(m)
            anon_nested.extend(anons)
        elif ch.type == "constructor_declaration":
            m, anons = _parse_method(
                ch, src, is_constructor=True, type_fqn=fqn,
                package=package, kind_by_simple=kind_by_simple,
                ctx=ctx,
                enclosing_type_node=enclosing_type_node,
                type_kind=kind,
                type_anns=annotations,
                file_rel=file_rel,
            )
            methods.append(m)
            anon_nested.extend(anons)
        elif ch.type in _TYPE_KINDS:
            nested.append(
                _parse_type(
                    ch, src,
                    package=package, outer_fqn=fqn, kind_by_simple=kind_by_simple,
                    file_rel=file_rel, ctx=ctx,
                ),
            )
    nested.extend(anon_nested)

    ann_names_set = {a.name for a in annotations}
    if (
        kind in ("class", "enum")
        and not any(m.is_constructor for m in methods)
        and not (_LOMBOK_RAC & ann_names_set)
    ):
        default_ctor_sig = "<init>()"
        methods.append(
            MethodDecl(
                name="<init>",
                return_type="",
                is_constructor=True,
                parameters=[],
                modifiers=[],
                annotations=[],
                signature=default_ctor_sig,
                start_byte=start_byte,
                end_byte=start_byte,
                start_line=start_line,
                end_line=start_line,
                call_sites=[],
                local_vars=[],
            )
        )

    name = fqn.rsplit(".", 1)[-1]
    type_decl = TypeDecl(
        name=name,
        kind=kind,
        fqn=fqn,
        modifiers=modifiers,
        annotations=annotations,
        extends=extends,
        implements=implements,
        fields=fields,
        methods=methods,
        nested=nested,
        start_byte=start_byte,
        end_byte=end_byte,
        start_line=start_line,
        end_line=end_line,
        outer_fqn=outer_fqn,
    )
    type_decl.capabilities = infer_capabilities_for_type(type_decl)
    return type_decl


def _parse_synthetic_anonymous_type(
    object_creation: Node,
    class_body: Node,
    src: bytes,
    *,
    package: str,
    host_type_fqn: str,
    kind_by_simple: dict[str, str],
    file_rel: str,
    ctx: _ParseCtx,
) -> TypeDecl:
    label = f"<anon:{object_creation.start_byte}>"
    fqn = f"{host_type_fqn}.{label}"
    type_node = object_creation.child_by_field_name("type")
    super_simple = _strip_type_to_simple(type_node, src) if type_node is not None else "Object"
    extends, implements = _anonymous_extends_implements(super_simple, kind_by_simple)
    return _parse_type_body_into_decl(
        class_body,
        src,
        package=package,
        fqn=fqn,
        kind="class",
        extends=extends,
        implements=implements,
        modifiers=[],
        annotations=[],
        kind_by_simple=kind_by_simple,
        start_byte=object_creation.start_byte,
        end_byte=object_creation.end_byte,
        start_line=object_creation.start_point[0] + 1,
        end_line=object_creation.end_point[0] + 1,
        outer_fqn=host_type_fqn,
        enclosing_type_node=None,
        file_rel=file_rel,
        ctx=ctx,
    )


def _extract_anonymous_types_in_subtree(
    root: Node,
    src: bytes,
    *,
    package: str,
    host_type_fqn: str,
    kind_by_simple: dict[str, str],
    file_rel: str,
    ctx: _ParseCtx,
) -> list[TypeDecl]:
    """Find every `new T() { }` with class_body under root; skip bodies (parsed separately)."""
    found: list[TypeDecl] = []

    def visit(n: Node) -> None:
        if n.type == "object_creation_expression":
            class_body: Node | None = None
            for ch in n.named_children:
                if ch.type == "class_body":
                    class_body = ch
                    break
            if class_body is not None:
                found.append(
                    _parse_synthetic_anonymous_type(
                        n, class_body, src,
                        package=package, host_type_fqn=host_type_fqn, kind_by_simple=kind_by_simple,
                        file_rel=file_rel,
                        ctx=ctx,
                    )
                )
            for ch in n.named_children:
                if ch.type == "class_body":
                    continue
                visit(ch)
            return
        for ch in n.children:
            visit(ch)

    visit(root)
    return found


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
    """Heuristic: ClassName.method() vs instance.method().

    TODO: Uppercase ``identifier`` receivers are treated as types; the graph
    builder may override via the per-method scope table when the name is a local.
    """
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
        chained_method_reference: bool = False,
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
                chained_method_reference=chained_method_reference,
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
            # Anonymous `new T() { }` bodies are indexed as synthetic nested types;
            # do not attribute their call sites to this caller_fqn (D3).
            for ch in n.named_children:
                if ch.type == "class_body":
                    continue
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
                lam=lam,
                chained_method_reference=chained,
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


def _unwrap_element_value(node: Node) -> Node:
    if node.type == "element_value" and node.named_children:
        return _unwrap_element_value(node.named_children[0])
    return node


def _record_route_skip(ctx: _ParseCtx) -> None:
    ctx.routes_skipped_unresolved += 1


def _string_value_atoms(val: Node, src: bytes, ctx: _ParseCtx) -> list[tuple[str, str, float, bool]]:
    """String-like values: (raw_text, resolution_strategy, confidence, resolved).

    Ladder (PR-A2): literal string without ``${`` → ``annotation`` / 1.0 / resolved;
    string containing ``${`` → ``spel`` / 0.85 / unresolved; anything else
    (identifier, binary expr, …) → ``constant_ref`` / 0.7 / unresolved.
    """
    val = _unwrap_element_value(val)
    if val.type == "string_literal":
        s = _string_literal_value(val, src)
        if s is None:
            _record_route_skip(ctx)
            return []
        if "${" in s:
            return [(s, "spel", 0.85, False)]
        return [(s, "annotation", 1.0, True)]
    if val.type in ("array_initializer", "element_value_array_initializer"):
        out: list[tuple[str, str, float, bool]] = []
        for ch in val.named_children:
            out.extend(_string_value_atoms(ch, src, ctx))
        return out
    raw = _txt(val, src).strip()
    if not raw:
        return []
    return [(raw, "constant_ref", 0.7, False)]


def _literal_strings_from_route_arg(val: Node, src: bytes, ctx: _ParseCtx) -> list[str]:
    """Literal-only slice (for @FeignClient name/url/path where SpEL is not modelled)."""
    return [a[0] for a in _string_value_atoms(val, src, ctx) if a[3]]


def _annotation_kv_nodes(ann: Node, src: bytes) -> tuple[dict[str, Node], Node | None]:
    pairs: dict[str, Node] = {}
    positional: Node | None = None
    alist = ann.child_by_field_name("arguments")
    if alist is None:
        for ch in ann.children:
            if ch.type == "annotation_argument_list":
                alist = ch
                break
    if alist is None:
        return pairs, positional
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
            pairs[_txt(key_node, src)] = val_node
        else:
            positional = ch
    return pairs, positional


def _extract_http_methods_from_arg(val: Node, src: bytes) -> list[str]:
    val = _unwrap_element_value(val)
    if val.type == "array_initializer":
        out: list[str] = []
        for ch in val.named_children:
            out.extend(_extract_http_methods_from_arg(ch, src))
        return out
    raw = _txt(val, src).strip()
    if not raw:
        return []
    simple = raw.rsplit(".", 1)[-1]
    return [simple.upper()] if simple else []


def _paths_and_methods_from_mapping_ann(
    ann: Node,
    src: bytes,
    simple_name: str,
    ctx: _ParseCtx,
) -> tuple[list[tuple[str, str, float, bool]], list[str]]:
    pairs, positional = _annotation_kv_nodes(ann, src)
    path_atoms: list[tuple[str, str, float, bool]] = []
    had_explicit_path_arg = False
    if "path" in pairs:
        had_explicit_path_arg = True
        path_atoms.extend(_string_value_atoms(pairs["path"], src, ctx))
    elif "value" in pairs:
        had_explicit_path_arg = True
        path_atoms.extend(_string_value_atoms(pairs["value"], src, ctx))
    elif positional is not None:
        had_explicit_path_arg = True
        path_atoms.extend(_string_value_atoms(positional, src, ctx))

    if simple_name == "GetMapping":
        methods = ["GET"]
    elif simple_name == "PostMapping":
        methods = ["POST"]
    elif simple_name == "PutMapping":
        methods = ["PUT"]
    elif simple_name == "DeleteMapping":
        methods = ["DELETE"]
    elif simple_name == "PatchMapping":
        methods = ["PATCH"]
    elif simple_name == "RequestMapping":
        methods = (
            _extract_http_methods_from_arg(pairs["method"], src)
            if "method" in pairs
            else [""]
        )
    else:
        methods = [""]

    if not path_atoms:
        if had_explicit_path_arg:
            return [], methods
        path_atoms = [("", "annotation", 1.0, True)]
    return path_atoms, methods


def _compose_http_paths(class_base: str, method_paths: list[str]) -> list[str]:
    """Join Feign / servlet context paths; always merge when `class_base` is set."""
    class_base = class_base.strip()
    out: list[str] = []
    for mp in method_paths:
        mp = mp.strip()
        if not class_base:
            p = mp if mp else "/"
        elif not mp:
            p = class_base
        else:
            joined = posixpath.normpath(f"{class_base.rstrip('/')}/{mp.lstrip('/')}")
            if not joined.startswith("/"):
                joined = "/" + joined
            p = joined
        out.append(p)
    return out


def _merge_http_route_with_class_base(
    class_base: str,
    method_path: str,
    method_strategy: str,
    method_confidence: float,
    method_resolved: bool,
) -> tuple[str, str, float, bool]:
    """Compose class-level + method path and derive final strategy/confidence."""
    full = _compose_http_paths(class_base, [method_path])[0]
    # Non-string annotation args stay ``constant_ref`` even if the expression text
    # contains ``${…}`` (e.g. string concat); SpEL applies only to string_literal.
    if method_strategy == "constant_ref":
        return full, "constant_ref", 0.7, False
    if method_strategy == "spel":
        return full, "spel", 0.85, False
    if "${" in full:
        return full, "spel", 0.85, False
    if class_base and "${" in class_base:
        return full, "spel", 0.85, False
    return full, method_strategy, method_confidence, method_resolved


def _type_level_request_mapping_base(enclosing_type_node: Node | None, src: bytes, ctx: _ParseCtx) -> str:
    if enclosing_type_node is None:
        return ""
    mods = _find_modifiers_child(enclosing_type_node)
    if mods is None:
        return ""
    for child in mods.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        simple, _ = _annotation_name(child, src)
        if simple != "RequestMapping":
            continue
        atoms, _ = _paths_and_methods_from_mapping_ann(child, src, simple, ctx)
        return atoms[0][0] if atoms else ""
    return ""


def _kafka_topics_from_ann_node(
    ann: Node, src: bytes, ctx: _ParseCtx,
) -> list[tuple[str, str, float, bool]]:
    pairs, positional = _annotation_kv_nodes(ann, src)
    if "topics" in pairs:
        return _string_value_atoms(pairs["topics"], src, ctx)
    if "topicPattern" in pairs:
        _record_route_skip(ctx)
        return []
    if positional is not None:
        return _string_value_atoms(positional, src, ctx)
    return []


def _rabbit_queues_from_ann_node(
    ann: Node, src: bytes, ctx: _ParseCtx,
) -> list[tuple[str, str, float, bool]]:
    pairs, positional = _annotation_kv_nodes(ann, src)
    if "queues" in pairs:
        return _string_value_atoms(pairs["queues"], src, ctx)
    if "bindings" in pairs:
        _record_route_skip(ctx)
        return []
    if positional is not None:
        return _string_value_atoms(positional, src, ctx)
    return []


def _jms_destination_from_ann_node(
    ann: Node, src: bytes, ctx: _ParseCtx,
) -> list[tuple[str, str, float, bool]]:
    pairs, positional = _annotation_kv_nodes(ann, src)
    for key in ("destination", "value"):
        if key in pairs:
            return _string_value_atoms(pairs[key], src, ctx)
    if positional is not None:
        return _string_value_atoms(positional, src, ctx)
    return []


def _stream_listener_destinations(
    ann: Node, src: bytes, ctx: _ParseCtx,
) -> list[tuple[str, str, float, bool]]:
    pairs, positional = _annotation_kv_nodes(ann, src)
    for key in ("value", "name"):
        if key in pairs:
            return _string_value_atoms(pairs[key], src, ctx)
    if positional is not None:
        return _string_value_atoms(positional, src, ctx)
    return []


def _collect_type_level_kafka_topics(enclosing_type_node: Node | None, src: bytes, ctx: _ParseCtx) -> list[str]:
    if enclosing_type_node is None:
        return []
    mods = _find_modifiers_child(enclosing_type_node)
    if mods is None:
        return []
    topics: list[str] = []
    for child in mods.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        simple, _ = _annotation_name(child, src)
        if simple != "KafkaListener":
            continue
        topics.extend(a[0] for a in _kafka_topics_from_ann_node(child, src, ctx) if a[3])
    return topics


def _collect_type_level_rabbit_queues(enclosing_type_node: Node | None, src: bytes, ctx: _ParseCtx) -> list[str]:
    if enclosing_type_node is None:
        return []
    mods = _find_modifiers_child(enclosing_type_node)
    if mods is None:
        return []
    qs: list[str] = []
    for child in mods.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        simple, _ = _annotation_name(child, src)
        if simple != "RabbitListener":
            continue
        qs.extend(a[0] for a in _rabbit_queues_from_ann_node(child, src, ctx) if a[3])
    return qs


def _enclosing_class_body_reactive(body: Node | None, src: bytes) -> bool:
    if body is None:
        return False
    for ch in body.named_children:
        if ch.type != "method_declaration":
            continue
        ret = ch.child_by_field_name("type")
        if ret is not None and _strip_type_to_simple(ret, src) in ("Mono", "Flux"):
            return True
        formal = ch.child_by_field_name("parameters")
        if formal is None:
            continue
        for p in formal.named_children:
            if p.type not in ("formal_parameter", "spread_parameter"):
                continue
            tnode = p.child_by_field_name("type")
            if tnode is not None and _strip_type_to_simple(tnode, src) in ("Mono", "Flux"):
                return True
    return False


def _http_framework_for_mapping(
    *,
    enclosing_body: Node | None,
    src: bytes,
    method_decl: MethodDecl,
    type_ann_names: set[str],
) -> str:
    if _enclosing_class_body_reactive(enclosing_body, src):
        return "webflux"
    if method_decl.return_type in ("Mono", "Flux"):
        return "webflux"
    if any(p.type_name in ("Mono", "Flux") for p in method_decl.parameters):
        return "webflux"
    if "RestController" in type_ann_names and _enclosing_class_body_reactive(enclosing_body, src):
        return "webflux"
    return "spring_mvc"


def _parse_feign_client_literals(enclosing_type_node: Node | None, src: bytes, ctx: _ParseCtx) -> tuple[str, str, str]:
    """Literal-only `name`, `url`, `path` from @FeignClient on the enclosing type."""
    if enclosing_type_node is None:
        return "", "", ""
    mods = _find_modifiers_child(enclosing_type_node)
    if mods is None:
        return "", "", ""
    for child in mods.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        simple, _ = _annotation_name(child, src)
        if simple != "FeignClient":
            continue
        pairs, positional = _annotation_kv_nodes(child, src)
        name_vals = _literal_strings_from_route_arg(pairs["name"], src, ctx) if "name" in pairs else []
        url_vals = _literal_strings_from_route_arg(pairs["url"], src, ctx) if "url" in pairs else []
        path_vals: list[str] = []
        if "path" in pairs:
            path_vals = _literal_strings_from_route_arg(pairs["path"], src, ctx)
        elif "value" in pairs:
            path_vals = _literal_strings_from_route_arg(pairs["value"], src, ctx)
        elif positional is not None:
            path_vals = _literal_strings_from_route_arg(positional, src, ctx)
        name = name_vals[0] if name_vals else ""
        url = url_vals[0] if url_vals else ""
        base_path = path_vals[0] if path_vals else ""
        return name, url, base_path
    return "", "", ""


def _method_has_bean_annotation(method_node: Node, src: bytes) -> bool:
    mods = _find_modifiers_child(method_node)
    if mods is None:
        return False
    for child in mods.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        simple, _ = _annotation_name(child, src)
        if simple == "Bean":
            return True
    return False


def _method_return_simple(method_node: Node, src: bytes) -> str:
    ret = method_node.child_by_field_name("type")
    return _strip_type_to_simple(ret, src) if ret is not None else ""


def _iter_method_annotation_nodes(method_node: Node, src: bytes) -> list[tuple[str, Node]]:
    mods = _find_modifiers_child(method_node)
    if mods is None:
        return []
    out: list[tuple[str, Node]] = []
    for child in mods.children:
        if child.type in ("marker_annotation", "annotation"):
            simple, _ = _annotation_name(child, src)
            out.append((simple, child))
    return out


def _maybe_emit_brownfield_exclusivity_shadowing(
    method_node: Node,
    src: bytes,
    *,
    ctx: _ParseCtx,
    method_fqn: str,
    file_rel: str,
    type_anns: list[AnnotationRef],
) -> None:
    """INFO when brownfield HTTP route/client co-exists with shadowable framework annotations."""
    if not ctx.verbose:
        return
    method_anns = _iter_method_annotation_nodes(method_node, src)
    has_bf_route = any(s in ("CodebaseHttpRoute", "CodebaseHttpRoutes") for s, _ in method_anns)
    has_bf_client = any(s in ("CodebaseHttpClient", "CodebaseHttpClients") for s, _ in method_anns)
    if not has_bf_route and not has_bf_client:
        return
    shadowed: list[str] = []
    for s, _ in method_anns:
        if s in _BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS:
            shadowed.append(s)
    type_names = {a.name for a in type_anns}
    if has_bf_client and "FeignClient" in type_names:
        shadowed.append("FeignClient")
    if not shadowed:
        return
    emit_brownfield_exclusivity_shadowing(
        method_fqn=method_fqn,
        file=file_rel,
        shadowed_framework_annotations=sorted(frozenset(shadowed)),
    )


def _parse_codebase_http_route_inner_annotation(
    ann: Node,
    src: bytes,
    ctx: _ParseCtx,
    *,
    handler_fqn: str,
    method_sig: str,
    file_rel: str,
    start_line: int,
    end_line: int,
) -> list[RouteDecl]:
    """One `@CodebaseHttpRoute(...)` element → `RouteDecl`(s)."""
    pairs, _ = _annotation_kv_nodes(ann, src)
    path_node = pairs.get("path")
    meth_arg = pairs.get("method")

    http_method = ""
    if meth_arg is not None:
        mv, mk = _annotation_value(meth_arg, src)
        if mv is not None:
            if mk == "enum":
                http_method = str(mv).upper()
            else:
                http_method = str(mv).strip().upper()
                emit_brownfield_method_string_literal(
                    method_fqn=handler_fqn,
                    file=file_rel,
                    reason="codebase_http_route_method_non_enum",
                )

    path_atoms: list[tuple[str, str, float, bool]] = []
    if path_node is not None:
        path_atoms = _string_value_atoms(path_node, src, ctx)
    if not path_atoms or not http_method:
        return []

    out: list[RouteDecl] = []
    for raw_path, _strat, conf, res in path_atoms:
        out.append(
            RouteDecl(
                method_fqn=handler_fqn,
                method_sig=method_sig,
                kind="http_endpoint",
                framework="spring_mvc",
                http_method=http_method,
                path=raw_path,
                topic="",
                broker="",
                feign_name="",
                feign_url="",
                resolution_strategy="codebase_route",
                confidence=conf,
                resolved=res,
                filename=file_rel,
                start_line=start_line,
                end_line=end_line,
                route_source_layer="layer_c_source",
            ),
        )
    return out


def _inner_annotation_nodes(container_ann: Node, src: bytes, target_simple: str) -> list[Node]:
    """Collect nested ``@<target_simple>`` annotations anywhere under ``container_ann``.

    Shared by the four brownfield container walkers — ``CodebaseHttpRoute``,
    ``CodebaseAsyncRoute``, ``CodebaseHttpClient``, ``CodebaseProducer`` — which
    differ only by the target annotation simple name.
    """
    found: list[Node] = []

    def visit(n: Node) -> None:
        if n.type == "annotation":
            name_node = n.child_by_field_name("name")
            n_simple = _txt(name_node, src).rsplit(".", 1)[-1] if name_node is not None else ""
            if n_simple == target_simple:
                found.append(n)
        for c in n.children:
            visit(c)

    visit(container_ann)
    return found


def _parse_codebase_http_client_annotation(
    ann: Node,
    src: bytes,
    ctx: _ParseCtx,
    *,
    method_fqn: str,
    method_sig: str,
    file_rel: str,
    start_line: int,
    end_line: int,
) -> OutgoingCallDecl:
    pairs, _ = _annotation_kv_nodes(ann, src)
    client_kind = ""
    if "clientKind" in pairs:
        val, vkind = _annotation_value(pairs["clientKind"], src)
        if val and vkind == "enum":
            kind_val = str(val)
            from java_ontology import VALID_CLIENT_KINDS  # deferred: java_ontology imports ast_java
            if kind_val in VALID_CLIENT_KINDS:
                client_kind = kind_val
            else:
                print(
                    f"[lancedb-mcp] CodebaseHttpClient: invalid clientKind {kind_val!r} — ignored",
                    file=sys.stderr,
                )
    target_service = ""
    if "targetService" in pairs:
        atoms = _string_value_atoms(pairs["targetService"], src, ctx)
        if atoms:
            target_service = atoms[0][0]
    path = ""
    if "path" in pairs:
        atoms = _string_value_atoms(pairs["path"], src, ctx)
        if atoms:
            path = _normalize_call_path(atoms[0][0]) if atoms[0][0] else ""
    method_call = ""
    if "method" in pairs:
        mnode = pairs["method"]
        mv, mk = _annotation_value(mnode, src)
        if mv is not None and mk == "enum":
            method_call = str(mv).upper()
        elif mv is not None:
            method_call = str(mv).strip().upper()
            emit_brownfield_method_string_literal(
                method_fqn=method_fqn,
                file=file_rel,
                reason="codebase_http_client_method_non_enum",
            )
        else:
            atoms = _string_value_atoms(mnode, src, ctx)
            if atoms:
                method_call = atoms[0][0].upper()
                emit_brownfield_method_string_literal(
                    method_fqn=method_fqn,
                    file=file_rel,
                    reason="codebase_http_client_method_non_enum",
                )
    return OutgoingCallDecl(
        method_fqn=method_fqn,
        method_sig=method_sig,
        client_kind=client_kind,
        channel="http",
        feign_target_name=target_service,
        feign_target_url="",
        path_template_call=path,
        method_call=method_call,
        topic_call="",
        broker_call="",
        raw_uri=path,
        raw_topic="",
        resolution_strategy="codebase_client",
        confidence_base=1.0,
        resolved=True,
        filename=file_rel,
        start_line=start_line,
        end_line=end_line,
    )


def _parse_codebase_producer_annotation(
    ann: Node,
    src: bytes,
    ctx: _ParseCtx,
    *,
    method_fqn: str,
    method_sig: str,
    file_rel: str,
    start_line: int,
    end_line: int,
) -> OutgoingCallDecl:
    pairs, _ = _annotation_kv_nodes(ann, src)
    client_kind = "kafka_send"
    kind_node = pairs.get("producerKind") or pairs.get("clientKind")
    if kind_node is not None:
        val, vkind = _annotation_value(kind_node, src)
        if val and vkind == "enum":
            kind_val = str(val)
            from java_ontology import VALID_PRODUCER_KINDS  # deferred: java_ontology imports ast_java
            if kind_val in VALID_PRODUCER_KINDS:
                client_kind = kind_val
            else:
                print(
                    f"[lancedb-mcp] CodebaseProducer: invalid producerKind {kind_val!r} — ignored",
                    file=sys.stderr,
                )
    topic = ""
    if "topic" in pairs:
        atoms = _string_value_atoms(pairs["topic"], src, ctx)
        if atoms:
            topic = atoms[0][0]
    broker = ""
    return OutgoingCallDecl(
        method_fqn=method_fqn,
        method_sig=method_sig,
        client_kind=client_kind,
        channel="async",
        feign_target_name="",
        feign_target_url="",
        path_template_call="",
        method_call="",
        topic_call=topic,
        broker_call=broker,
        raw_uri="",
        raw_topic=topic,
        resolution_strategy="codebase_producer",
        confidence_base=1.0,
        resolved=True,
        filename=file_rel,
        start_line=start_line,
        end_line=end_line,
    )


def _field_types_for_type(type_node: Node | None, src: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if type_node is None:
        return out
    body = type_node.child_by_field_name("body")
    if body is None:
        return out
    for ch in body.named_children:
        if ch.type != "field_declaration":
            continue
        tnode = ch.child_by_field_name("type")
        if tnode is None:
            continue
        tname = _strip_type_to_simple(tnode, src)
        for dc in ch.named_children:
            if dc.type != "variable_declarator":
                continue
            nnode = dc.child_by_field_name("name")
            if nnode is not None:
                out[_txt(nnode, src)] = tname
    return out


def _collect_plus_literals(node: Node, src: bytes) -> list[str]:
    vals: list[str] = []
    if node.type == "binary_expression":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        op = node.child_by_field_name("operator")
        op_txt = _txt(op, src) if op is not None else ""
        if op_txt == "+" and left is not None and right is not None:
            vals.extend(_collect_plus_literals(left, src))
            vals.extend(_collect_plus_literals(right, src))
            return vals
    lit = _string_literal_value(node, src)
    if lit is not None:
        vals.append(lit)
    return vals


def _normalize_call_path(raw_path: str) -> str:
    p = (raw_path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p


def _outgoing_calls_from_codebase_http_client_producer_annotations(
    method_node: Node,
    src: bytes,
    *,
    method_fqn: str,
    method_decl: MethodDecl,
    file_rel: str,
    ctx: _ParseCtx,
) -> list[OutgoingCallDecl]:
    """Brownfield @CodebaseHttpClient(s) / @CodebaseProducer(s) on the method itself.

    Must run even when the method has no body (interfaces, abstract methods).
    """
    out: list[OutgoingCallDecl] = []
    for simple, ann in _iter_method_annotation_nodes(method_node, src):
        if simple == "CodebaseHttpClient":
            out.append(
                _parse_codebase_http_client_annotation(
                    ann,
                    src,
                    ctx,
                    method_fqn=method_fqn,
                    method_sig=method_decl.signature,
                    file_rel=file_rel,
                    start_line=method_decl.start_line,
                    end_line=method_decl.end_line,
                ),
            )
        elif simple == "CodebaseHttpClients":
            for inner in _inner_annotation_nodes(ann, src, "CodebaseHttpClient"):
                out.append(
                    _parse_codebase_http_client_annotation(
                        inner,
                        src,
                        ctx,
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        file_rel=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    ),
                )
        elif simple == "CodebaseProducer":
            out.append(
                _parse_codebase_producer_annotation(
                    ann,
                    src,
                    ctx,
                    method_fqn=method_fqn,
                    method_sig=method_decl.signature,
                    file_rel=file_rel,
                    start_line=method_decl.start_line,
                    end_line=method_decl.end_line,
                ),
            )
        elif simple == "CodebaseProducers":
            for inner in _inner_annotation_nodes(ann, src, "CodebaseProducer"):
                out.append(
                    _parse_codebase_producer_annotation(
                        inner,
                        src,
                        ctx,
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        file_rel=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    ),
                )
    return out


def _collect_outgoing_calls(
    method_node: Node,
    type_node: Node | None,
    src: bytes,
    *,
    ctx: _ParseCtx,
    project_root: str,
    method_decl: MethodDecl,
    type_fqn: str,
    file_rel: str,
) -> list[OutgoingCallDecl]:
    del project_root
    out: list[OutgoingCallDecl] = []
    method_fqn = f"{type_fqn}#{method_decl.signature}"
    type_mods = _find_modifiers_child(type_node) if type_node is not None else None
    type_ann_names: set[str] = set()
    feign_target_name = ""
    feign_target_url = ""
    feign_base_path = ""
    if type_mods is not None:
        for child in type_mods.children:
            if child.type not in ("marker_annotation", "annotation"):
                continue
            simple, _ = _annotation_name(child, src)
            type_ann_names.add(simple)
    feign_target_name, feign_target_url, feign_base_path = _parse_feign_client_literals(type_node, src, ctx)
    if type_node is not None and type_node.type == "interface_declaration" and "FeignClient" in type_ann_names:
        method_call = ""
        path_template = ""
        for simple, ann_node in _iter_method_annotation_nodes(method_node, src):
            if simple not in _ROUTE_HTTP_MAPPING_NAMES:
                continue
            path_atoms, methods = _paths_and_methods_from_mapping_ann(ann_node, src, simple, ctx)
            if methods:
                method_call = methods[0]
            if path_atoms:
                composed = _compose_http_paths(feign_base_path, [path_atoms[0][0]])[0]
                path_template = _normalize_call_path(composed)
            break
        out.append(
            OutgoingCallDecl(
                method_fqn=method_fqn,
                method_sig=method_decl.signature,
                client_kind="feign_method",
                channel="http",
                feign_target_name=feign_target_name,
                feign_target_url=feign_target_url,
                path_template_call=path_template,
                method_call=method_call,
                topic_call="",
                broker_call="",
                raw_uri=path_template,
                raw_topic="",
                resolution_strategy="feign_method",
                confidence_base=1.0,
                resolved=True,
                filename=file_rel,
                start_line=method_decl.start_line,
                end_line=method_decl.end_line,
            )
        )

    ann_out = _outgoing_calls_from_codebase_http_client_producer_annotations(
        method_node,
        src,
        method_fqn=method_fqn,
        method_decl=method_decl,
        file_rel=file_rel,
        ctx=ctx,
    )
    body = method_node.child_by_field_name("body")
    if body is None:
        out.extend(ann_out)
        return out
    receiver_types: dict[str, str] = {}
    receiver_types.update(_field_types_for_type(type_node, src))
    for p in method_decl.parameters:
        receiver_types[p.name] = p.type_name
    for n, t in method_decl.local_vars:
        receiver_types[n] = t

    rest_methods = {
        "getForObject": "GET",
        "getForEntity": "GET",
        "postForEntity": "POST",
        "postForObject": "POST",
        "put": "PUT",
        "delete": "DELETE",
    }
    web_methods = {"get", "post", "put", "delete", "patch"}

    def _receiver_type(obj: Node | None) -> str:
        if obj is None:
            return ""
        if obj.type == "identifier":
            return receiver_types.get(_txt(obj, src), "")
        return ""

    def visit(n: Node) -> None:
        if n.type == "method_invocation":
            obj = n.child_by_field_name("object")
            name_node = n.child_by_field_name("name")
            args = n.child_by_field_name("arguments")
            mname = _txt(name_node, src) if name_node is not None else ""
            recv_type = _receiver_type(obj)
            recv_txt = _txt(obj, src) if obj is not None else ""
            arg_nodes = args.named_children if args is not None else []
            if recv_type == "RestTemplate" and mname in (set(rest_methods) | {"exchange"}) and arg_nodes:
                first = arg_nodes[0]
                atoms = _string_value_atoms(first, src, ctx)
                method_call = rest_methods.get(mname, "")
                if mname == "exchange" and len(arg_nodes) > 1:
                    raw = _txt(arg_nodes[1], src).strip()
                    if raw.startswith("HttpMethod."):
                        method_call = raw.rsplit(".", 1)[-1].upper()
                path_template = ""
                strategy = "rest_template"
                conf = 0.3
                resolved = False
                raw_uri = _txt(first, src)
                force_unresolved = first.type in ("method_invocation", "lambda_expression", "ternary_expression")
                if atoms:
                    val, strat, base_conf, is_resolved = atoms[0]
                    path_template = _normalize_call_path(val) if val.startswith("/") else ""
                    strategy = "rest_template"
                    conf = base_conf
                    resolved = is_resolved
                if force_unresolved:
                    path_template = ""
                    conf = 0.3
                    resolved = False
                if first.type == "binary_expression":
                    lits = [s for s in _collect_plus_literals(first, src) if s.startswith("/")]
                    if lits:
                        path_template = _normalize_call_path(lits[-1])
                        conf = 0.7
                        strategy = "rest_template"
                        resolved = False
                out.append(
                    OutgoingCallDecl(
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        client_kind="rest_template",
                        channel="http",
                        feign_target_name="",
                        feign_target_url="",
                        path_template_call=path_template,
                        method_call=method_call,
                        topic_call="",
                        broker_call="",
                        raw_uri=raw_uri,
                        raw_topic="",
                        resolution_strategy=strategy,
                        confidence_base=conf,
                        resolved=resolved and bool(path_template),
                        filename=file_rel,
                        start_line=n.start_point[0] + 1,
                        end_line=n.end_point[0] + 1,
                    )
                )
            elif recv_type == "KafkaTemplate" and mname == "send" and arg_nodes:
                first = arg_nodes[0]
                atoms = _string_value_atoms(first, src, ctx)
                topic = ""
                conf = 0.3
                resolved = False
                if atoms:
                    topic, _s, conf, resolved = atoms[0]
                out.append(
                    OutgoingCallDecl(
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        client_kind="kafka_send",
                        channel="async",
                        feign_target_name="",
                        feign_target_url="",
                        path_template_call="",
                        method_call="",
                        topic_call=topic,
                        broker_call="",
                        raw_uri="",
                        raw_topic=_txt(first, src),
                        resolution_strategy="kafka_template",
                        confidence_base=conf,
                        resolved=resolved,
                        filename=file_rel,
                        start_line=n.start_point[0] + 1,
                        end_line=n.end_point[0] + 1,
                    )
                )
            elif recv_type == "WebClient" and mname in web_methods:
                out.append(
                    OutgoingCallDecl(
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        client_kind="web_client",
                        channel="http",
                        feign_target_name="",
                        feign_target_url="",
                        path_template_call="",
                        method_call=mname.upper(),
                        topic_call="",
                        broker_call="",
                        raw_uri=recv_txt,
                        raw_topic="",
                        resolution_strategy="unresolved",
                        confidence_base=0.3,
                        resolved=False,
                        filename=file_rel,
                        start_line=n.start_point[0] + 1,
                        end_line=n.end_point[0] + 1,
                    )
                )
            elif recv_type == "StreamBridge" and mname == "send":
                out.append(
                    OutgoingCallDecl(
                        method_fqn=method_fqn,
                        method_sig=method_decl.signature,
                        client_kind="stream_bridge_send",
                        channel="async",
                        feign_target_name="",
                        feign_target_url="",
                        path_template_call="",
                        method_call="",
                        topic_call="",
                        broker_call="",
                        raw_uri="",
                        raw_topic=_txt(n, src),
                        resolution_strategy="unresolved",
                        confidence_base=0.3,
                        resolved=False,
                        filename=file_rel,
                        start_line=n.start_point[0] + 1,
                        end_line=n.end_point[0] + 1,
                    )
                )
        for c in n.children:
            visit(c)

    visit(body)
    out.extend(ann_out)
    return out


def _collect_routes(
    method_node: Node,
    enclosing_type_node: Node | None,
    src: bytes,
    *,
    type_fqn: str,
    type_kind: str,
    type_anns: list[AnnotationRef],
    method_decl: MethodDecl,
    signature: str,
    file_rel: str,
    ctx: _ParseCtx,
) -> list[RouteDecl]:
    """Extract RouteDecl literals from Spring mapping / messaging annotations.

    WebFlux vs Spring MVC: same annotations; framework is ``webflux`` when the
    enclosing type exposes reactive signatures (Mono/Flux) — otherwise
    ``spring_mvc`` (PR-A1 plan).
    """
    routes: list[RouteDecl] = []
    handler_fqn = f"{type_fqn}#{signature}"
    type_ann_names = {a.name for a in type_anns}
    enclosing_body = enclosing_type_node.child_by_field_name("body") if enclosing_type_node else None

    ann_nodes = _iter_method_annotation_nodes(method_node, src)

    # --- Spring Cloud Stream-style @Bean handler ---
    if _method_has_bean_annotation(method_node, src):
        ret_simple = _method_return_simple(method_node, src)
        if ret_simple in ("Function", "Consumer", "Supplier"):
            routes.append(
                RouteDecl(
                    method_fqn=handler_fqn,
                    method_sig=signature,
                    kind="stream_binding",
                    framework="stream",
                    http_method="",
                    path="",
                    topic="",
                    broker="",
                    feign_name="",
                    feign_url="",
                    resolution_strategy="annotation",
                    confidence=1.0,
                    resolved=True,
                    filename=file_rel,
                    start_line=method_decl.start_line,
                    end_line=method_decl.end_line,
                )
            )

    class_kafka_topics = _collect_type_level_kafka_topics(enclosing_type_node, src, ctx)
    class_rabbit_queues = _collect_type_level_rabbit_queues(enclosing_type_node, src, ctx)

    # --- Messaging annotations on method ---
    for simple, node in ann_nodes:
        if simple == "KafkaListener":
            topic_atoms = _kafka_topics_from_ann_node(node, src, ctx)
            if not topic_atoms and class_kafka_topics:
                topic_atoms = [(t, "annotation", 1.0, True) for t in class_kafka_topics]
            for tp, strat, conf, res in topic_atoms:
                routes.append(
                    RouteDecl(
                        method_fqn=handler_fqn,
                        method_sig=signature,
                        kind="kafka_topic",
                        framework="kafka",
                        http_method="",
                        path="",
                        topic=tp,
                        broker="",
                        feign_name="",
                        feign_url="",
                        resolution_strategy=strat,
                        confidence=conf,
                        resolved=res,
                        filename=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    )
                )
        elif simple == "RabbitListener":
            queue_atoms = _rabbit_queues_from_ann_node(node, src, ctx)
            if not queue_atoms and class_rabbit_queues:
                queue_atoms = [(q, "annotation", 1.0, True) for q in class_rabbit_queues]
            for q, strat, conf, res in queue_atoms:
                routes.append(
                    RouteDecl(
                        method_fqn=handler_fqn,
                        method_sig=signature,
                        kind="rabbit_queue",
                        framework="rabbitmq",
                        http_method="",
                        path="",
                        topic=q,
                        broker="",
                        feign_name="",
                        feign_url="",
                        resolution_strategy=strat,
                        confidence=conf,
                        resolved=res,
                        filename=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    )
                )
        elif simple == "JmsListener":
            for dest, strat, conf, res in _jms_destination_from_ann_node(node, src, ctx):
                routes.append(
                    RouteDecl(
                        method_fqn=handler_fqn,
                        method_sig=signature,
                        kind="jms_destination",
                        framework="jms",
                        http_method="",
                        path="",
                        topic=dest,
                        broker="",
                        feign_name="",
                        feign_url="",
                        resolution_strategy=strat,
                        confidence=conf,
                        resolved=res,
                        filename=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    )
                )
        elif simple == "StreamListener":
            for dest, strat, conf, res in _stream_listener_destinations(node, src, ctx):
                routes.append(
                    RouteDecl(
                        method_fqn=handler_fqn,
                        method_sig=signature,
                        kind="stream_binding",
                        framework="stream",
                        http_method="",
                        path="",
                        topic=dest,
                        broker="",
                        feign_name="",
                        feign_url="",
                        resolution_strategy=strat,
                        confidence=conf,
                        resolved=res,
                        filename=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    )
                )

    # --- HTTP mappings ---
    feign_iface = type_kind == "interface" and _type_has_feign_client(type_anns)
    http_base = _type_level_request_mapping_base(enclosing_type_node, src, ctx)
    feign_name, feign_url, feign_base_path = _parse_feign_client_literals(enclosing_type_node, src, ctx)

    for simple, node in ann_nodes:
        if simple not in _ROUTE_HTTP_MAPPING_NAMES:
            continue
        path_atoms, methods = _paths_and_methods_from_mapping_ann(node, src, simple, ctx)
        if not path_atoms:
            continue
        class_path_prefix = http_base if not feign_iface else feign_base_path
        if feign_iface:
            continue
        fw = _http_framework_for_mapping(
            enclosing_body=enclosing_body,
            src=src,
            method_decl=method_decl,
            type_ann_names=type_ann_names,
        )
        kind = "http_endpoint"
        for raw_path, m_strat, m_conf, m_res in path_atoms:
            full_path, f_strat, f_conf, f_res = _merge_http_route_with_class_base(
                class_path_prefix, raw_path, m_strat, m_conf, m_res,
            )
            for hm in methods:
                routes.append(
                    RouteDecl(
                        method_fqn=handler_fqn,
                        method_sig=signature,
                        kind=kind,
                        framework=fw,
                        http_method=hm,
                        path=full_path,
                        topic="",
                        broker="",
                        feign_name=feign_name if feign_iface else "",
                        feign_url=feign_url if feign_iface else "",
                        resolution_strategy=f_strat,
                        confidence=f_conf,
                        resolved=f_res,
                        filename=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    )
                )

    # --- @CodebaseHttpRoute / @CodebaseHttpRoutes + @CodebaseAsyncRoute(s) ---
    for simple, node in ann_nodes:
        if simple == "CodebaseHttpRoute":
            routes.extend(
                _parse_codebase_http_route_inner_annotation(
                    node,
                    src,
                    ctx,
                    handler_fqn=handler_fqn,
                    method_sig=signature,
                    file_rel=file_rel,
                    start_line=method_decl.start_line,
                    end_line=method_decl.end_line,
                ),
            )
        elif simple == "CodebaseHttpRoutes":
            for inner in _inner_annotation_nodes(node, src, "CodebaseHttpRoute"):
                routes.extend(
                    _parse_codebase_http_route_inner_annotation(
                        inner,
                        src,
                        ctx,
                        handler_fqn=handler_fqn,
                        method_sig=signature,
                        file_rel=file_rel,
                        start_line=method_decl.start_line,
                        end_line=method_decl.end_line,
                    ),
                )
        elif simple in ("CodebaseAsyncRoute", "CodebaseAsyncRoutes"):
            nodes = [node]
            if simple == "CodebaseAsyncRoutes":
                nodes = list(_inner_annotation_nodes(node, src, "CodebaseAsyncRoute"))
            for ann in nodes:
                pairs, _ = _annotation_kv_nodes(ann, src)
                topic_node = pairs.get("topic")
                if topic_node is None:
                    continue
                topic_atoms = _string_value_atoms(topic_node, src, ctx)
                for topic, strat, conf, res in topic_atoms:
                    routes.append(
                        RouteDecl(
                            method_fqn=handler_fqn,
                            method_sig=signature,
                            kind="kafka_topic",
                            framework="kafka",
                            http_method="",
                            path="",
                            topic=topic,
                            broker="",
                            feign_name="",
                            feign_url="",
                            resolution_strategy=strat,
                            confidence=conf,
                            resolved=res,
                            filename=file_rel,
                            start_line=method_decl.start_line,
                            end_line=method_decl.end_line,
                            route_source_layer="layer_c_source",
                        )
                    )

    return routes


def _type_has_feign_client(type_anns: list[AnnotationRef]) -> bool:
    return any(a.name == "FeignClient" for a in type_anns)


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


def _parse_method(
    node: Node,
    src: bytes,
    *,
    is_constructor: bool,
    type_fqn: str,
    package: str,
    kind_by_simple: dict[str, str],
    ctx: _ParseCtx,
    enclosing_type_node: Node | None,
    type_kind: str,
    type_anns: list[AnnotationRef],
    file_rel: str,
) -> tuple[MethodDecl, list[TypeDecl]]:
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
    anon_nested: list[TypeDecl] = []
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
        anon_nested = _extract_anonymous_types_in_subtree(
            body, src, package=package, host_type_fqn=type_fqn, kind_by_simple=kind_by_simple,
            file_rel=file_rel,
            ctx=ctx,
        )
    if not is_constructor:
        m.routes = _collect_routes(
            node,
            enclosing_type_node,
            src,
            type_fqn=type_fqn,
            type_kind=type_kind,
            type_anns=type_anns,
            method_decl=m,
            signature=signature,
            file_rel=file_rel,
            ctx=ctx,
        )
        m.outgoing_calls = _collect_outgoing_calls(
            node,
            enclosing_type_node,
            src,
            ctx=ctx,
            project_root="",
            method_decl=m,
            type_fqn=type_fqn,
            file_rel=file_rel,
        )
        _maybe_emit_brownfield_exclusivity_shadowing(
            node,
            src,
            ctx=ctx,
            method_fqn=caller_fqn,
            file_rel=file_rel,
            type_anns=type_anns,
        )
    return m, anon_nested


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


def _parse_type(
    node: Node,
    src: bytes,
    *,
    package: str,
    outer_fqn: str | None,
    kind_by_simple: dict[str, str],
    file_rel: str,
    ctx: _ParseCtx,
) -> TypeDecl:
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
    if body is None:
        td = TypeDecl(
            name=name,
            kind=kind,
            fqn=fqn,
            modifiers=mods,
            annotations=anns,
            extends=extends,
            implements=implements,
            fields=[],
            methods=[],
            nested=[],
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            outer_fqn=outer_fqn,
        )
        td.capabilities = infer_capabilities_for_type(td)
        return td
    return _parse_type_body_into_decl(
        body,
        src,
        package=package,
        fqn=fqn,
        kind=kind,
        extends=extends,
        implements=implements,
        modifiers=mods,
        annotations=anns,
        kind_by_simple=kind_by_simple,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        outer_fqn=outer_fqn,
        enclosing_type_node=node,
        file_rel=file_rel,
        ctx=ctx,
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


def parse_java(source: bytes | str, *, filename: str = "", verbose: bool = False) -> JavaFileAst:
    """Parse a Java file into a JavaFileAst. Never raises on invalid source."""
    if isinstance(source, str):
        src = source.encode("utf-8", errors="replace")
    else:
        src = source

    ctx = _ParseCtx(verbose=verbose)
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
        routes_skipped_unresolved=0,
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
    kind_by_simple = _pre_scan_declared_type_kinds(root, src)
    file_rel = filename
    for child in root.named_children:
        if child.type in _TYPE_KINDS:
            top_types.append(
                _parse_type(
                    child, src,
                    package=package, outer_fqn=None, kind_by_simple=kind_by_simple,
                    file_rel=file_rel,
                    ctx=ctx,
                ),
            )

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
        routes_skipped_unresolved=ctx.routes_skipped_unresolved,
    )


def infer_role(annotation_names: Iterable[str]) -> str:
    """Map a set of simple annotation names to a single role. First hit wins."""
    for ann in annotation_names:
        role = ROLE_ANNOTATIONS.get(ann)
        if role:
            return role
    return "OTHER"


def _type_injects_messaging(type_decl: "TypeDecl") -> bool:
    """True when the type injects a messaging template via field or constructor."""
    for fld in type_decl.fields:
        if fld.type_name in _INJECTED_TYPES_TO_CAPABILITY:
            return True
    for method in type_decl.methods:
        if method.is_constructor:
            for p in method.parameters:
                if p.type_name in _INJECTED_TYPES_TO_CAPABILITY:
                    return True
    return False


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

    # Types injecting messaging templates are outbound callers (CLIENT role),
    # symmetric with CONTROLLER covering both HTTP and messaging inbound.
    if _type_injects_messaging(type_decl):
        return "CLIENT"

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
