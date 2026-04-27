package com.bank.chat.assign.integration;

import com.bank.chat.assign.config.AssignProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestTemplate;

import java.util.HashMap;
import java.util.Map;

@Component
public class ChatCoreJoinClient {

    private static final Logger log = LoggerFactory.getLogger(ChatCoreJoinClient.class);

    private final AssignProperties assignProperties;
    private final RestTemplate restTemplate;

    public ChatCoreJoinClient(AssignProperties assignProperties, RestTemplate assignRestTemplate) {
        this.assignProperties = assignProperties;
        this.restTemplate = assignRestTemplate;
    }

    public void joinOperator(String conversationId, String operatorId, String epkId) {
        String base = assignProperties.getChatCore().getBaseUrl().replaceAll("/$", "");
        String url = base + "/chat/joinOperator";

        Map<String, Object> body = new HashMap<>();
        body.put("conversationId", conversationId);
        body.put("operatorId", operatorId);
        if (StringUtils.hasText(epkId)) {
            body.put("epkId", epkId);
        }

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        String token = assignProperties.getChatCore().getInternalToken();
        if (StringUtils.hasText(token)) {
            headers.set("X-Chat-Internal-Token", token);
        }

        try {
            restTemplate.postForEntity(url, new HttpEntity<>(body, headers), Void.class);
        } catch (Exception ex) {
            log.warn("chat-core joinOperator failed: {}", ex.toString());
            throw ex;
        }
    }
}
