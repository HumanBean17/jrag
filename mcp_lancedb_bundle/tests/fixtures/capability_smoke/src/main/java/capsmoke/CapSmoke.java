package capsmoke;

import org.springframework.kafka.annotation.KafkaListener;

/** Minimal class for capability smoke test: MESSAGE_LISTENER from @KafkaListener. */
public class CapSmoke {
    @KafkaListener(topics = "smoke-topic")
    public void onMessage(String payload) {
    }
}
