"""Layer C `@CodebaseAsyncRoute` replaces same-method `@KafkaListener` auto routes."""
from __future__ import annotations

from java_codebase_rag.graph.graph_enrich import BrownfieldOverrides, resolve_routes_for_method

from java_codebase_rag.ast.ast_java import parse_java


def _type_and_method(src: str):
    ast = parse_java(src.encode("utf-8"))
    assert ast.top_level_types, ast.parse_error
    t = ast.top_level_types[0]
    assert t.methods, "expected a method"
    return t, t.methods[0]


def test_codebase_async_route_drops_kafka_listener_builtin() -> None:
    src = """
package x;
import org.springframework.kafka.annotation.KafkaListener;
class M {
  @KafkaListener(topics = "${kafka.messenger.topic.name}")
  @com.example.rag.CodebaseAsyncRoute(topic = "MESSENGER.V1")
  void handleMessage(String x) {}
}
"""
    t, m = _type_and_method(src)
    out = resolve_routes_for_method(
        method_decl=m,
        enclosing_type=t,
        overrides=BrownfieldOverrides({}, {}, {}, {}, {}, {}),
        meta_chain=None,
        builtin_routes=m.routes,
    )
    assert len(out) == 1
    assert out[0].kind == "kafka_topic"
    assert out[0].topic == "MESSENGER.V1"
    assert out[0].route_source_layer == "layer_c_source"


def test_kafka_listener_only_unchanged() -> None:
    src = """
package x;
import org.springframework.kafka.annotation.KafkaListener;
class L {
  @KafkaListener(topics = "orders")
  void onMsg(String x) {}
}
"""
    t, m = _type_and_method(src)
    out = resolve_routes_for_method(
        method_decl=m,
        enclosing_type=t,
        overrides=BrownfieldOverrides({}, {}, {}, {}, {}, {}),
        meta_chain=None,
        builtin_routes=m.routes,
    )
    assert len(out) == 1
    assert out[0].topic == "orders"
    assert out[0].route_source_layer == "builtin"


def test_multi_topic_kafka_listener_replaced_by_one_async_brownfield() -> None:
    src = """
package x;
import org.springframework.kafka.annotation.KafkaListener;
class M {
  @KafkaListener(topics = {"a", "b"})
  @com.example.rag.CodebaseAsyncRoute(topic = "OVERRIDE")
  void m(String x) {}
}
"""
    t, m = _type_and_method(src)
    out = resolve_routes_for_method(
        method_decl=m,
        enclosing_type=t,
        overrides=BrownfieldOverrides({}, {}, {}, {}, {}, {}),
        meta_chain=None,
        builtin_routes=m.routes,
    )
    assert len(out) == 1
    assert out[0].topic == "OVERRIDE"


def test_codebase_async_route_only_still_emits_route() -> None:
    src = """
package x;
class M {
  @com.example.rag.CodebaseAsyncRoute(topic = "ONLY.BF")
  void m(String x) {}
}
"""
    t, m = _type_and_method(src)
    out = resolve_routes_for_method(
        method_decl=m,
        enclosing_type=t,
        overrides=BrownfieldOverrides({}, {}, {}, {}, {}, {}),
        meta_chain=None,
        builtin_routes=m.routes,
    )
    assert len(out) == 1
    assert out[0].topic == "ONLY.BF"
