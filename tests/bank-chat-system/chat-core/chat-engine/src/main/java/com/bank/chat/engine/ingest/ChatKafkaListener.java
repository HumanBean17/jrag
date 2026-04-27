package com.bank.chat.engine.ingest;

import com.bank.chat.contracts.ChatTopics;
import com.bank.chat.contracts.InternalEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class ChatKafkaListener {

    private static final Logger log = LoggerFactory.getLogger(ChatKafkaListener.class);

    private final ChatOrchestrationService orchestrationService;

    public ChatKafkaListener(ChatOrchestrationService orchestrationService) {
        this.orchestrationService = orchestrationService;
    }

    @KafkaListener(
            topics = ChatTopics.INCOMING,
            containerFactory = "chatKafkaListenerContainerFactory"
    )
    public void onIncoming(InternalEvent event) {
        try {
            orchestrationService.handle(event);
        } catch (Exception ex) {
            log.error("Failed to process event {}", event != null ? event.getCorrelationId() : "null", ex);
            throw ex;
        }
    }
}
