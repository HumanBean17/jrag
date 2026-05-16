package orolla.abstractproducer;

import org.springframework.kafka.core.KafkaTemplate;

public class ConcreteProducerApi extends AbstractProducerApi {
    private final KafkaTemplate<String, String> kafkaTemplate;

    public ConcreteProducerApi(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void publish() {
        kafkaTemplate.send("orders", "payload");
    }
}
