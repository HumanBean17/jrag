package com.bank.chat.engine.notification;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class PushGatewayClient {

    private final RestTemplate restTemplate;

    public PushGatewayClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public void sendPush(String recipient, String payload) {
        Map<String, String> request = new HashMap<>();
        request.put("recipient", recipient);
        request.put("payload", payload);
        restTemplate.postForEntity("http://push-gateway/api/v1/push/deliver", request, Void.class);
    }
}
