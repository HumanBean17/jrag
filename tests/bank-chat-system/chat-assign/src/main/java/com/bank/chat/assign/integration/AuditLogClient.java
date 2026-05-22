package com.bank.chat.assign.integration;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

@Component
public class AuditLogClient {

    private final RestTemplate restTemplate;

    public AuditLogClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public void logAssignment(String conversationId, String operatorId) {
        restTemplate.postForEntity("http://audit-service/api/v1/audit/log",
            Map.of("conversationId", conversationId, "operatorId", operatorId, "action", "ASSIGN"),
            Void.class);
    }
}
