"""Shared valid role and capability label sets for Java indexers and MCP.

Used by `ast_java` inference, brownfield config validation in `graph_enrich`,
and resolver steps for `@CodebaseRole` / `@CodebaseCapability`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ast_java import (
    ROLE_ANNOTATIONS,
    _INJECTED_TYPES_TO_CAPABILITY,
    _METHOD_ANN_TO_CAPABILITY,
    _SUPERTYPE_TO_CAPABILITY,
    _TYPE_ANN_TO_CAPABILITY,
)

# Roles: Spring stereotype values plus DTO from `infer_role_for_type`.
VALID_ROLES: frozenset[str] = frozenset((*ROLE_ANNOTATIONS.values(), "DTO"))

VALID_CAPABILITIES: frozenset[str] = frozenset(
    {
        *_METHOD_ANN_TO_CAPABILITY.values(),
        *_TYPE_ANN_TO_CAPABILITY.values(),
        *_INJECTED_TYPES_TO_CAPABILITY.values(),
        *_SUPERTYPE_TO_CAPABILITY.values(),
    }
)

VALID_ROUTE_FRAMEWORKS: frozenset[str] = frozenset((
    "spring_mvc",
    "webflux",
))

VALID_ROUTE_KINDS: frozenset[str] = frozenset((
    "http_endpoint",
    "http_consumer",
    "kafka_topic",
    "rabbit_queue",
    "jms_destination",
    "stream_binding",
))

VALID_CLIENT_KINDS: frozenset[str] = frozenset((
    "feign_method",
    "rest_template",
    "web_client",
))

VALID_PRODUCER_KINDS: frozenset[str] = frozenset((
    "kafka_send",
    "stream_bridge_send",
))

VALID_HTTP_CALL_STRATEGIES: frozenset[str] = frozenset((
    "feign_method",
    "rest_template",
    "web_client",
    "unresolved",
))

VALID_ASYNC_CALL_STRATEGIES: frozenset[str] = frozenset((
    "kafka_template",
    "stream_bridge",
    "rabbit_template",
    "jms_template",
    "unresolved",
))

VALID_HTTP_CALL_MATCHES: frozenset[str] = frozenset((
    "cross_service",
    "intra_service",
    "ambiguous",
    "phantom",
    "unresolved",
))

VALID_RESOLVE_REASONS: frozenset[str] = frozenset((
    "exact_id",
    "exact_fqn",
    "fqn_suffix",
    "short_name",
    "route_template",
    "route_method_path",
    "client_target",
    "client_target_path",
))

# Brownfield / fallback edge resolution strategies (hints v2 neighbors fuzzy signal).
FUZZY_STRATEGY_SET: frozenset[str] = frozenset({
    "layer_c_source",
    "layer_b_fqn",
    "phantom",
    "chained_receiver",
    "overload_ambiguous",
    "implicit_super",
})

# Union of fuzzy + non-fuzzy resolver strategies that may appear on graph edges
# carrying a `strategy` column (brownfield layers, codebase stubs, call-graph tiers,
# HTTP/async dispatch literals). Used by `EdgeSpec.brownfield_resolver_sourced`.
BROWNFIELD_RESOLVER_STRATEGY_SET: frozenset[str] = frozenset({
    *FUZZY_STRATEGY_SET,
    "layer_b_ann",
    "layer_a_meta",
    "codebase_route",
    "codebase_client",
    "codebase_producer",
    "annotation",
    "spel",
    "constant_ref",
    *VALID_HTTP_CALL_STRATEGIES,
    *VALID_ASYNC_CALL_STRATEGIES,
    *VALID_CLIENT_KINDS,
    *VALID_PRODUCER_KINDS,
    "import_map",
    "static_import",
    "static_import_wildcard",
    "constructor",
    "method_reference",
    "this_super",
    "unique_type_name",
    "suffix",
    "same_module",
})

NodeKind = Literal["Symbol", "Route", "Client", "Producer"]
Cardinality = Literal["many_to_many", "many_to_one", "one_to_many", "one_to_one"]


@dataclass(frozen=True)
class EdgeAttr:
    name: str
    kuzu_type: str
    purpose: str


@dataclass(frozen=True)
class EdgeSpec:
    name: str
    src: NodeKind
    dst: NodeKind
    cardinality: Cardinality
    brownfield_resolver_sourced: bool
    attrs: tuple[EdgeAttr, ...]
    purpose: str
    typical_traversals: dict[str, str]
    member_only: bool = False


_SYMBOL_TYPE_TRAVERSAL = (
    "neighbors(['{id}'],'out',['DECLARES']) "
    "then neighbors(member_ids,'{direction}',['{edge}'])"
)

EDGE_SCHEMA: dict[str, EdgeSpec] = {
    "EXTENDS": EdgeSpec(
        name="EXTENDS",
        src="Symbol",
        dst="Symbol",
        cardinality="many_to_one",
        brownfield_resolver_sourced=False,
        attrs=(
            EdgeAttr("dst_name", "STRING", "raw supertype name as written in source"),
            EdgeAttr("dst_fqn", "STRING", "best-effort resolved FQN of the supertype"),
            EdgeAttr("resolved", "BOOLEAN", "True iff dst_fqn was resolved to an in-graph Symbol"),
        ),
        purpose="class or interface direct supertype relation",
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="EXTENDS"),
            "member_subject": "neighbors(['{id}'],'out',['EXTENDS'])",
            "alien_subject": "EXTENDS connects Symbol → Symbol; use a type or member Symbol id",
        },
    ),
    "IMPLEMENTS": EdgeSpec(
        name="IMPLEMENTS",
        src="Symbol",
        dst="Symbol",
        cardinality="many_to_many",
        brownfield_resolver_sourced=False,
        attrs=(
            EdgeAttr("dst_name", "STRING", "raw interface name as written in source"),
            EdgeAttr("dst_fqn", "STRING", "best-effort resolved FQN of the interface"),
            EdgeAttr("resolved", "BOOLEAN", "True iff dst_fqn was resolved to an in-graph Symbol"),
        ),
        purpose="class implements interface relation",
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="IMPLEMENTS"),
            "member_subject": "neighbors(['{id}'],'out',['IMPLEMENTS'])",
            "alien_subject": "IMPLEMENTS connects Symbol → Symbol; use a type or member Symbol id",
        },
    ),
    "INJECTS": EdgeSpec(
        name="INJECTS",
        src="Symbol",
        dst="Symbol",
        cardinality="many_to_many",
        brownfield_resolver_sourced=False,
        attrs=(
            EdgeAttr("dst_name", "STRING", "raw injected type name as written in source"),
            EdgeAttr("dst_fqn", "STRING", "best-effort resolved FQN of the injected type"),
            EdgeAttr("resolved", "BOOLEAN", "True iff dst_fqn was resolved to an in-graph Symbol"),
            EdgeAttr("mechanism", "STRING", "injection mechanism literal (constructor, field, setter, …)"),
            EdgeAttr("annotation", "STRING", "injection annotation simple name when present"),
            EdgeAttr("field_or_param", "STRING", "field or parameter name for the injection site"),
        ),
        purpose="dependency injection edge from declaring type to injected type",
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="INJECTS"),
            "member_subject": "neighbors(['{id}'],'in',['INJECTS'])",
            "alien_subject": "INJECTS connects Symbol → Symbol; use a type Symbol id",
        },
    ),
    "DECLARES": EdgeSpec(
        name="DECLARES",
        src="Symbol",
        dst="Symbol",
        cardinality="one_to_many",
        brownfield_resolver_sourced=False,
        attrs=(),
        purpose="type declares member Symbol (method, constructor, nested type)",
        typical_traversals={
            "type_subject": "neighbors(['{id}'],'out',['DECLARES'])",
            "member_subject": "neighbors(['{id}'],'in',['DECLARES'])",
            "alien_subject": "DECLARES connects Symbol → Symbol; use a type Symbol id for outbound members",
        },
    ),
    "OVERRIDES": EdgeSpec(
        name="OVERRIDES",
        src="Symbol",
        dst="Symbol",
        cardinality="many_to_one",
        brownfield_resolver_sourced=False,
        attrs=(),
        purpose="subtype method overrides supertype declared method with matching signature",
        member_only=True,
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="OVERRIDES"),
            "member_subject": "neighbors(['{id}'],'out',['OVERRIDES'])",
            "alien_subject": "OVERRIDES connects method Symbol → method Symbol",
        },
    ),
    "CALLS": EdgeSpec(
        name="CALLS",
        src="Symbol",
        dst="Symbol",
        cardinality="many_to_many",
        brownfield_resolver_sourced=True,
        attrs=(
            EdgeAttr("call_site_line", "INT64", "source line of the call site"),
            EdgeAttr("call_site_byte", "INT64", "source byte offset of the call site"),
            EdgeAttr("arg_count", "INT64", "argument count at the call site (-1 for method references)"),
            EdgeAttr("confidence", "DOUBLE", "resolver confidence in [0.0, 1.0]"),
            EdgeAttr("strategy", "STRING", "call-graph resolution strategy literal"),
            EdgeAttr("source", "STRING", "call-graph source tag"),
            EdgeAttr("resolved", "BOOLEAN", "True iff callee Symbol was resolved in-graph"),
        ),
        purpose="intra-codebase method call from caller method to callee method",
        member_only=True,
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="CALLS"),
            "member_subject": "neighbors(['{id}'],'out',['CALLS'])",
            "alien_subject": "CALLS connects method Symbol → method Symbol",
        },
    ),
    "EXPOSES": EdgeSpec(
        name="EXPOSES",
        src="Symbol",
        dst="Route",
        cardinality="one_to_one",
        brownfield_resolver_sourced=True,
        attrs=(
            EdgeAttr("confidence", "DOUBLE", "route extraction confidence in [0.0, 1.0]"),
            EdgeAttr("strategy", "STRING", "route resolution strategy literal"),
        ),
        purpose="declaring method exposes an inbound HTTP or messaging Route",
        member_only=True,
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(id="{id}", direction="{direction}", edge="EXPOSES"),
            "member_subject": "neighbors(['{id}'],'out',['EXPOSES'])",
            "alien_subject": "EXPOSES connects method Symbol → Route; use a method Symbol id",
        },
    ),
    "DECLARES_CLIENT": EdgeSpec(
        name="DECLARES_CLIENT",
        src="Symbol",
        dst="Client",
        cardinality="one_to_many",
        brownfield_resolver_sourced=True,
        attrs=(
            EdgeAttr("confidence", "DOUBLE", "client declaration confidence in [0.0, 1.0]"),
            EdgeAttr("strategy", "STRING", "client resolution strategy literal"),
        ),
        purpose="method declares an outbound HTTP client call site",
        member_only=True,
        typical_traversals={
            "type_subject": _SYMBOL_TYPE_TRAVERSAL.format(
                id="{id}", direction="{direction}", edge="DECLARES_CLIENT",
            ),
            "member_subject": "neighbors(['{id}'],'out',['DECLARES_CLIENT'])",
            "alien_subject": "DECLARES_CLIENT connects method Symbol → Client",
        },
    ),
    "HTTP_CALLS": EdgeSpec(
        name="HTTP_CALLS",
        src="Symbol",
        dst="Route",
        cardinality="many_to_many",
        brownfield_resolver_sourced=True,
        attrs=(
            EdgeAttr("confidence", "DOUBLE", "pass6 match confidence in [0.0, 1.0]"),
            EdgeAttr("strategy", "STRING", "HTTP call resolution strategy literal"),
            EdgeAttr("method_call", "STRING", "HTTP method of the call site"),
            EdgeAttr("raw_uri", "STRING", "uninterpolated URI template from the call site"),
            EdgeAttr("match", "STRING", "cross_service|intra_service|ambiguous|phantom|unresolved"),
        ),
        purpose="resolved HTTP call from declaring method to target route (pre-flip: Symbol→Route; PR-B: Client→Route)",
        typical_traversals={
            "type_subject_current": (
                "neighbors(['{id}'],'out',['DECLARES']) "
                "then neighbors(member_ids,'out',['HTTP_CALLS'])"
            ),
            "type_subject": (
                "neighbors(['{id}'],'out',['DECLARES']) "
                "then neighbors(member_ids,'out',['DECLARES_CLIENT']) "
                "then neighbors(client_ids,'out',['HTTP_CALLS'])"
            ),
            "member_subject_current": "neighbors(['{id}'],'out',['HTTP_CALLS'])",
            "member_subject": (
                "neighbors(['{id}'],'out',['DECLARES_CLIENT']) "
                "then neighbors(client_ids,'out',['HTTP_CALLS'])"
            ),
            "alien_subject": (
                "HTTP_CALLS is Symbol→Route until PR-B; use member_subject_current. "
                "After PR-B (Client→Route), use member_subject via DECLARES_CLIENT"
            ),
        },
    ),
    "ASYNC_CALLS": EdgeSpec(
        name="ASYNC_CALLS",
        src="Symbol",
        dst="Route",
        cardinality="many_to_many",
        brownfield_resolver_sourced=True,
        attrs=(
            EdgeAttr("confidence", "DOUBLE", "pass6 match confidence in [0.0, 1.0]"),
            EdgeAttr("strategy", "STRING", "async call resolution strategy literal"),
            EdgeAttr("direction", "STRING", "produce|consume async direction literal"),
            EdgeAttr("raw_topic", "STRING", "uninterpolated topic template from the call site"),
            EdgeAttr("match", "STRING", "cross_service|intra_service|ambiguous|phantom|unresolved"),
        ),
        purpose="resolved async call from declaring method to topic route (pre-flip: Symbol→Route; PR-C: Producer→Route)",
        typical_traversals={
            "type_subject_current": (
                "neighbors(['{id}'],'out',['DECLARES']) "
                "then neighbors(member_ids,'out',['ASYNC_CALLS'])"
            ),
            "type_subject": (
                "neighbors(['{id}'],'out',['DECLARES']) "
                "then neighbors(member_ids,'out',['DECLARES_PRODUCER']) "
                "then neighbors(producer_ids,'out',['ASYNC_CALLS'])"
            ),
            "member_subject_current": "neighbors(['{id}'],'out',['ASYNC_CALLS'])",
            "member_subject": (
                "neighbors(['{id}'],'out',['DECLARES_PRODUCER']) "
                "then neighbors(producer_ids,'out',['ASYNC_CALLS'])"
            ),
            "alien_subject": (
                "ASYNC_CALLS is Symbol→Route until PR-C; use member_subject_current. "
                "After PR-C (Producer→Route), use member_subject via DECLARES_PRODUCER"
            ),
        },
    ),
}

ResolveReason = Literal[
    "exact_id",
    "exact_fqn",
    "fqn_suffix",
    "short_name",
    "route_template",
    "route_method_path",
    "client_target",
    "client_target_path",
]

__all__ = [
    "VALID_ROLES",
    "VALID_CAPABILITIES",
    "VALID_ROUTE_FRAMEWORKS",
    "VALID_ROUTE_KINDS",
    "VALID_CLIENT_KINDS",
    "VALID_PRODUCER_KINDS",
    "VALID_HTTP_CALL_STRATEGIES",
    "VALID_ASYNC_CALL_STRATEGIES",
    "VALID_HTTP_CALL_MATCHES",
    "VALID_RESOLVE_REASONS",
    "FUZZY_STRATEGY_SET",
    "BROWNFIELD_RESOLVER_STRATEGY_SET",
    "NodeKind",
    "Cardinality",
    "EdgeAttr",
    "EdgeSpec",
    "EDGE_SCHEMA",
    "ResolveReason",
]
