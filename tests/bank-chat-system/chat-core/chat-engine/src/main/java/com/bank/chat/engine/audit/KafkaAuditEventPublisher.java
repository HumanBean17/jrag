package com.bank.chat.engine.audit;

import com.bank.chat.domain.AuditEntry;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class KafkaAuditEventPublisher extends AbstractAuditEventPublisher {

    private final KafkaTemplate<String, AuditEntry> kafkaTemplate;

    public KafkaAuditEventPublisher(KafkaTemplate<String, AuditEntry> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    protected void deliver(AuditEntry entry) {
        kafkaTemplate.send("banking.chat.audit", entry.getAction(), entry);
    }
}
