package com.bank.chat.engine.notification;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.domain.NotificationAudit;
import com.bank.chat.domain.NotificationAuditRepository;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Component
public class EmailNotificationSender extends AbstractNotificationSender implements Auditable, Measurable {

    private final NotificationAuditRepository repository;
    private final FollowUpKafkaPublisher publisher;

    public EmailNotificationSender(NotificationAuditRepository repository, FollowUpKafkaPublisher publisher) {
        this.repository = repository;
        this.publisher = publisher;
    }

    @Override
    public String channel() {
        return "EMAIL";
    }

    @Override
    protected void doSend(String recipient, String payload) {
        NotificationAudit audit = new NotificationAudit();
        audit.setChannel("EMAIL");
        audit.setRecipient(recipient);
        audit.setPayload(payload);
        audit.setStatus("SENT");
        audit.setSentAt(Instant.now());
        repository.save(audit);
    }

    @Override
    protected String enrich(String message) {
        return "[BANK-NOTICE] " + message;
    }

    @Override
    protected void audit(String recipient, String payload) {
        Map<String, String> metadata = new HashMap<>();
        metadata.put("recipient", recipient);
        metadata.put("channel", "EMAIL");
        InternalEvent event = InternalEvent.create(
                UUID.randomUUID().toString(),
                "email-audit-" + UUID.randomUUID().toString(),
                null,
                null,
                EventType.ACK,
                payload,
                metadata
        );
        publisher.publishOperatorNotification(event);
    }

    @Override
    public String getTrackingId() {
        return "email-notification";
    }

    @Override
    public void recordAudit(String action) {
        NotificationAudit audit = new NotificationAudit();
        audit.setChannel("EMAIL");
        audit.setRecipient("system");
        audit.setPayload(action);
        audit.setStatus("AUDITED");
        audit.setSentAt(Instant.now());
        repository.save(audit);
    }

    @Override
    public long getDeliveryLatencyMs() {
        return 0L;
    }
}
