package smoke.a;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class OrderListener {
    @KafkaListener(topics = "orders")
    public void onOrder(String payload) {
    }
}
