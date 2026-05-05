from __future__ import annotations

from ast_java import parse_java


def _calls(src: str, *, type_name: str, method_name: str):
    ast = parse_java(src.encode(), filename="Smoke.java")
    t = next(x for x in ast.all_types if x.name == type_name)
    m = next(x for x in t.methods if x.name == method_name)
    return m.outgoing_calls


def test_feign_method_caller_emits_outgoing_call() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.cloud.openfeign.FeignClient;
        import org.springframework.web.bind.annotation.GetMapping;
        @FeignClient(name = "user-svc")
        interface C { @GetMapping("/x") String get(); }
        """,
        type_name="C",
        method_name="get",
    )
    assert len(calls) == 1
    assert calls[0].client_kind == "feign_method"
    assert calls[0].feign_target_name == "user-svc"


def test_rest_template_get_for_object_literal() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        class C { RestTemplate restTemplate; String m(){ return restTemplate.getForObject("/api/users", String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.client_kind == "rest_template"
    assert c.path_template_call == "/api/users"
    assert c.method_call == "GET"
    assert c.confidence_base == 1.0


def test_rest_template_exchange_with_http_method_const() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        import org.springframework.http.HttpMethod;
        class C { RestTemplate restTemplate; void m(){ restTemplate.exchange("/x", HttpMethod.PUT, null, String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    assert calls[0].method_call == "PUT"


def test_rest_template_post_for_entity_string_concat_tail() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        class C { RestTemplate restTemplate; void m(String base){ restTemplate.postForEntity(base + "/chat/joinOperator", null, String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.path_template_call == "/chat/joinOperator"
    assert c.confidence_base == 0.7
    assert c.resolved is False
    assert "+" in c.raw_uri


def test_rest_template_spel_uri() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        class C { RestTemplate restTemplate; void m(){ restTemplate.getForObject("${api.path}", String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.confidence_base == 0.85
    assert c.resolution_strategy == "rest_template"
    assert c.resolved is False


def test_rest_template_constant_ref_uri() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        class Endpoints { static final String USERS = "/u"; }
        class C { RestTemplate restTemplate; void m(){ restTemplate.getForObject(Endpoints.USERS, String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    assert calls[0].confidence_base == 0.7
    assert calls[0].resolved is False


def test_rest_template_unresolved_uri_method_call() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.client.RestTemplate;
        class C { RestTemplate restTemplate; String buildUri(){ return "/x"; } void m(){ restTemplate.getForObject(buildUri(), String.class); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.resolution_strategy == "rest_template"
    assert c.confidence_base == 0.3
    assert c.resolved is False
    assert c.path_template_call == ""


def test_kafka_template_send_literal() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.kafka.core.KafkaTemplate;
        class C { KafkaTemplate<String,String> kafkaTemplate; void m(){ kafkaTemplate.send("orders", "x"); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.client_kind == "kafka_send"
    assert c.topic_call == "orders"
    assert c.confidence_base == 1.0


def test_kafka_template_send_constant_ref() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.kafka.core.KafkaTemplate;
        class ChatTopics { static final String INCOMING = "incoming"; }
        class C { KafkaTemplate<String,String> kafkaTemplate; void m(){ kafkaTemplate.send(ChatTopics.INCOMING, "x"); } }
        """,
        type_name="C",
        method_name="m",
    )
    assert calls[0].topic_call == "ChatTopics.INCOMING"
    assert calls[0].confidence_base == 0.7
    assert calls[0].resolved is False


def test_web_client_chain_emits_unresolved_v1() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.web.reactive.function.client.WebClient;
        class C { WebClient webClient; void m(){ webClient.get().uri("/x").retrieve(); } }
        """,
        type_name="C",
        method_name="m",
    )
    c = calls[0]
    assert c.client_kind == "web_client"
    assert c.resolution_strategy == "unresolved"
    assert c.confidence_base == 0.3


def test_stream_bridge_emits_unresolved_v1() -> None:
    calls = _calls(
        """
        package smoke;
        import org.springframework.cloud.stream.function.StreamBridge;
        class C { StreamBridge streamBridge; void m(Object p){ streamBridge.send("binding-out-0", p); } }
        """,
        type_name="C",
        method_name="m",
    )
    assert calls[0].resolution_strategy == "unresolved"


def test_unknown_receiver_type_silently_skipped() -> None:
    calls = _calls(
        """
        package smoke;
        class C { void m(Object someObj){ someObj.send("x"); } }
        """,
        type_name="C",
        method_name="m",
    )
    assert calls == []
