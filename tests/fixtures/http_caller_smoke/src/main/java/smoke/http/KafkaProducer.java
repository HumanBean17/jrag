package smoke.http;

import org.springframework.kafka.core.KafkaTemplate;

public class KafkaProducer {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public KafkaProducer(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void sendLiteral(String payload) {
        kafkaTemplate.send("orders", payload);
    }

    public void sendConst(String payload) {
        kafkaTemplate.send(TopicNames.ORDERS, payload);
    }
}
