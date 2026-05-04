"""Shared valid role and capability label sets for Java indexers and MCP.

Used by `ast_java` inference, brownfield config validation in `graph_enrich`,
and resolver steps for `@CodebaseRole` / `@CodebaseCapability`."""
from __future__ import annotations

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
    "feign",
    "kafka",
    "rabbitmq",
    "jms",
    "stream",
))

VALID_ROUTE_KINDS: frozenset[str] = frozenset((
    "http_endpoint",
    "http_consumer",
    "kafka_topic",
    "rabbit_queue",
    "jms_destination",
    "stream_binding",
))

__all__ = ["VALID_ROLES", "VALID_CAPABILITIES", "VALID_ROUTE_FRAMEWORKS", "VALID_ROUTE_KINDS"]
