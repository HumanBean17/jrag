package com.bank.chat.engine.kafka;

import com.bank.chat.contracts.ChatTopics;
import com.bank.chat.contracts.InternalEvent;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class FollowUpKafkaPublisher {

    private final KafkaTemplate<String, InternalEvent> kafkaTemplate;

    public FollowUpKafkaPublisher(KafkaTemplate<String, InternalEvent> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishIncoming(InternalEvent event) {
        kafkaTemplate.send(ChatTopics.INCOMING, event.getConversationId(), event);
    }

    public void publishOperatorNotification(InternalEvent event) {
        kafkaTemplate.send(ChatTopics.OPERATOR_NOTIFICATIONS, event.getConversationId(), event);
    }

    public void publishComplianceReview(InternalEvent event) {
        kafkaTemplate.send(ChatTopics.COMPLIANCE_REVIEW, event.getConversationId(), event);
    }

    public void publishEscalation(InternalEvent event) {
        kafkaTemplate.send(ChatTopics.ESCALATION, event.getConversationId(), event);
    }
}
