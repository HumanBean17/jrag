"""Unit tests for infer_capabilities_for_type in ast_java.py.

All tests use synthetic, minimal Java snippets — no fixture corpus required.
Each test names the specific detector path it exercises.
"""
from __future__ import annotations

from ast_java import parse_java


def _caps(source: str) -> list[str]:
    """Parse a Java snippet and return capabilities of the first top-level type."""
    ast = parse_java(source)
    assert ast.top_level_types, "No top-level type parsed"
    return ast.top_level_types[0].capabilities


def test_kafka_listener_method_yields_message_listener() -> None:
    src = """
    @Service
    public class OrderConsumer {
        @KafkaListener(topics = "orders")
        public void onOrder(String msg) {}
    }
    """
    assert _caps(src) == ["MESSAGE_LISTENER"]


def test_kafka_listener_and_scheduled_both_present() -> None:
    src = """
    @Component
    public class MultiTasker {
        @KafkaListener(topics = "events")
        public void onEvent(String e) {}

        @Scheduled(fixedRate = 1000)
        public void poll() {}
    }
    """
    caps = _caps(src)
    assert "MESSAGE_LISTENER" in caps
    assert "SCHEDULED_TASK" in caps
    assert caps == sorted(caps), "capabilities must be sorted"


def test_service_with_kafka_template_field_is_producer_not_listener() -> None:
    src = """
    @Service
    public class NotificationService {
        @Autowired
        private KafkaTemplate<String, String> kafkaTemplate;
    }
    """
    ast = parse_java(src)
    t = ast.top_level_types[0]
    assert t.capabilities == ["MESSAGE_PRODUCER"]
    # role should still be SERVICE (separate axis)
    from ast_java import infer_role_for_type
    assert infer_role_for_type(t) == "SERVICE"


def test_service_with_kafka_template_constructor_param_is_producer() -> None:
    src = """
    @Service
    public class PublisherService {
        private final KafkaTemplate<String, Object> kt;

        public PublisherService(KafkaTemplate<String, Object> kt) {
            this.kt = kt;
        }
    }
    """
    assert _caps(src) == ["MESSAGE_PRODUCER"]


def test_service_with_listener_and_producer_both_present() -> None:
    src = """
    @Service
    public class BridgeService {
        @Autowired
        private KafkaTemplate<String, String> out;

        @KafkaListener(topics = "in")
        public void onIn(String msg) {}
    }
    """
    caps = _caps(src)
    assert "MESSAGE_LISTENER" in caps
    assert "MESSAGE_PRODUCER" in caps
    assert caps == sorted(caps)


def test_rest_controller_advice_yields_exception_handler() -> None:
    src = """
    @RestControllerAdvice
    public class GlobalExceptionHandler {
        @ExceptionHandler(RuntimeException.class)
        public ResponseEntity<?> handle(RuntimeException ex) {
            return ResponseEntity.badRequest().build();
        }
    }
    """
    caps = _caps(src)
    assert "EXCEPTION_HANDLER" in caps


def test_class_implementing_job_yields_scheduled_task() -> None:
    src = """
    public class ReportJob implements Job {
        @Override
        public void execute(JobExecutionContext ctx) {}
    }
    """
    assert _caps(src) == ["SCHEDULED_TASK"]


def test_plain_service_no_special_annotations_empty_capabilities() -> None:
    src = """
    @Service
    public class PlainService {
        private final SomeRepository repo;

        public PlainService(SomeRepository repo) {
            this.repo = repo;
        }

        public void doWork() {}
    }
    """
    assert _caps(src) == []


def test_capabilities_sorted_and_deduplicated() -> None:
    """Even if multiple methods trigger the same capability it appears once."""
    src = """
    @Service
    public class MultiListener {
        @KafkaListener(topics = "a")
        public void onA(String m) {}

        @RabbitListener(queues = "b")
        public void onB(String m) {}

        @KafkaListener(topics = "c")
        public void onC(String m) {}
    }
    """
    caps = _caps(src)
    assert caps == ["MESSAGE_LISTENER"]
    assert caps == sorted(set(caps))
