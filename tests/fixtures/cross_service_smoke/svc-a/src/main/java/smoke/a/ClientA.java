package smoke.a;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.client.RestTemplate;

@FeignClient(name = "svc-b")
interface BFeignClient {
    @PostMapping("/chat/joinOperator")
    String joinOperator();
}

public class ClientA {
    RestTemplate restTemplate;
    WebClient webClient;
    KafkaTemplate<String, String> kafkaTemplate;
    BFeignClient bFeignClient;

    public void callCrossService() {
        restTemplate.postForEntity("/chat/joinOperator", null, String.class);
    }

    public void callAmbiguousPath() {
        restTemplate.postForEntity("/api/users", null, String.class);
    }

    public void callExternal() {
        restTemplate.postForEntity("https://external.com/api/x", null, String.class);
    }

    public void callUnresolved() {
        webClient.get().uri(buildUri()).retrieve();
    }

    public void callFeign() {
        bFeignClient.joinOperator();
    }

    public void produce() {
        kafkaTemplate.send("orders", "payload");
    }

    private String buildUri() {
        return "/dynamic";
    }
}
