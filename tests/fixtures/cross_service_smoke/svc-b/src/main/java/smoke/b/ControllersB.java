package smoke.b;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
class JoinControllerB {
    @PostMapping("/chat/joinOperator")
    public String joinOperator() {
        return "ok";
    }
}

@RestController
class UsersControllerB {
    @PostMapping("/api/users")
    public String createUser() {
        return "ok";
    }
}

class OrdersListenerB {
    @KafkaListener(topics = "orders")
    public void onOrder(String payload) {
    }
}
