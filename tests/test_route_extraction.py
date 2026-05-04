"""PR-A1 route literal extraction (`ast_java`) and path canonicalisation (`build_ast_graph`)."""
from __future__ import annotations

from ast_java import parse_java
from build_ast_graph import _normalize_path, _route_id


def _routes(src: str, *, filename: str = "Smoke.java") -> list:
    ast = parse_java(src.encode(), filename=filename)
    assert ast.all_types, ast.parse_error
    return ast.all_types[0].methods[0].routes


def test_case1_get_mapping_rest_controller_spring_mvc() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @GetMapping("/users")
  String m() { return ""; }
}
'''
    routes = _routes(src)
    assert len(routes) == 1
    r = routes[0]
    assert r.framework == "spring_mvc"
    assert r.http_method == "GET"
    assert r.path == "/users"


def test_case2_request_mapping_post_enum() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @RequestMapping(value="/api", method = RequestMethod.POST)
  void m() {}
}
'''
    routes = _routes(src)
    assert len(routes) == 1
    assert routes[0].http_method == "POST"
    assert routes[0].path == "/api"


def test_case3_class_and_method_request_mapping_concat() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/v1")
class C {
  @GetMapping("/users")
  String m() { return ""; }
}
'''
    routes = _routes(src)
    assert len(routes) == 1
    assert routes[0].path == "/api/v1/users"


def test_case4_request_mapping_path_array_two_routes() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @RequestMapping(path = {"/a", "/b"})
  void m() {}
}
'''
    routes = _routes(src)
    assert len(routes) == 2
    paths = {routes[0].path, routes[1].path}
    assert paths == {"/a", "/b"}


def test_case5_mono_return_webflux_framework() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
import reactor.core.publisher.Mono;
@RestController
class C {
  @GetMapping("/x")
  Mono<String> m() { return Mono.empty(); }
}
'''
    routes = _routes(src)
    assert len(routes) == 1
    assert routes[0].framework == "webflux"


def test_case6_feign_client_three_methods() -> None:
    src = '''
package x;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.*;
@FeignClient(name = "user-svc", url = "", path = "/users")
interface Api {
  @GetMapping("/{id}") Object a(@PathVariable("id") String id);
  @GetMapping("/extra") Object b();
}
'''
    ast = parse_java(src.encode(), filename="Api.java")
    methods = ast.all_types[0].methods
    routes = []
    for m in methods:
        routes.extend(m.routes)
    assert len(methods) == 2
    by_path = {r.path for r in routes}
    assert "/users/{id}" in by_path
    assert "/users/extra" in by_path
    assert all(r.framework == "feign" for r in routes)
    assert all(r.feign_name == "user-svc" for r in routes)


def test_case7_kafka_listener_literal_topic() -> None:
    src = '''
package x;
import org.springframework.kafka.annotation.KafkaListener;
class L {
  @KafkaListener(topics = "orders")
  void onMsg(String x) {}
}
'''
    routes = _routes(src)
    assert len(routes) == 1
    assert routes[0].kind == "kafka_topic"
    assert routes[0].framework == "kafka"
    assert routes[0].topic == "orders"
    assert routes[0].http_method == ""


def test_case8_kafka_spel_skipped_counts() -> None:
    src = '''
package x;
import org.springframework.kafka.annotation.KafkaListener;
class L {
  @KafkaListener(topics = "${app.topic}")
  void onMsg(String x) {}
}
'''
    ast = parse_java(src.encode())
    assert ast.all_types[0].methods[0].routes == []
    assert ast.routes_skipped_unresolved >= 1


def test_case9_constant_ref_get_mapping_skipped_counts() -> None:
    src = '''
package x;
import org.springframework.web.bind.annotation.*;
@RestController
class C {
  @GetMapping(Endpoints.USERS)
  void m() {}
}
interface Endpoints { String USERS = "/x"; }
'''
    ast = parse_java(src.encode())
    c_type = next(t for t in ast.all_types if t.name == "C")
    assert c_type.methods[0].routes == []
    assert ast.routes_skipped_unresolved >= 1


def test_case10_normalize_path_two_vars() -> None:
    t, rx = _normalize_path("/api/users/{id}/orders/{oid}")
    assert t == "/api/users/{}/orders/{}"
    assert rx == "^/api/users/[^/]+/orders/[^/]+/?$"


def test_case11_normalize_path_regex_constraint() -> None:
    t, rx = _normalize_path("/api/users/{id:\\d+}")
    assert t == "/api/users/{}"
    assert rx == "^/api/users/\\d+/?$"


def test_route_id_stable() -> None:
    a = _route_id("spring_mvc", "http_endpoint", "GET", "/x", "", "", "svc-a")
    b = _route_id("spring_mvc", "http_endpoint", "GET", "/x", "", "", "svc-a")
    c = _route_id("spring_mvc", "http_endpoint", "GET", "/x", "", "", "svc-b")
    assert a == b
    assert a != c
