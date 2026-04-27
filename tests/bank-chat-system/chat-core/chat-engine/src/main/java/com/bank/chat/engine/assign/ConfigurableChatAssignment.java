package com.bank.chat.engine.assign;

import com.bank.chat.contracts.AssignmentRequest;
import com.bank.chat.engine.config.ChatEngineProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class ConfigurableChatAssignment implements ChatAssignmentPort {

    private static final Logger log = LoggerFactory.getLogger(ConfigurableChatAssignment.class);

    private final ChatEngineProperties properties;
    private final RestTemplate restTemplate;

    public ConfigurableChatAssignment(ChatEngineProperties properties) {
        this.properties = properties;
        this.restTemplate = new RestTemplate();
    }

    @Override
    public void requestAssignment(AssignmentRequest request) {
        String base = properties.getChatAssign().getBaseUrl();
        if (base == null || base.isBlank()) {
            log.info("Assignment (no HTTP): conversation={} epk={} priority={} afterHours={}",
                    request.getConversationId(), request.getEpkId(), request.getPriorityScore(),
                    request.isAfterHoursQueued());
            return;
        }
        String url = base.endsWith("/") ? base + "chat/assign" : base + "/chat/assign";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        try {
            restTemplate.postForEntity(url, new HttpEntity<>(request, headers), Void.class);
        } catch (Exception ex) {
            log.warn("Assignment HTTP failed: {}", ex.toString());
        }
    }
}
