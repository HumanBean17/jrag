package com.bank.chat.engine.notification;

import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.audit.AuditEventPublisher;
import com.bank.chat.engine.kafka.EventStreamBridge;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class NotificationService {

    private final EmailNotificationSender emailSender;
    private final PushNotificationSender pushSender;
    private final EventStreamBridge streamBridge;
    private final AuditEventPublisher auditPublisher;

    public NotificationService(EmailNotificationSender emailSender,
                               PushNotificationSender pushSender,
                               EventStreamBridge streamBridge,
                               AuditEventPublisher auditPublisher) {
        this.emailSender = emailSender;
        this.pushSender = pushSender;
        this.streamBridge = streamBridge;
        this.auditPublisher = auditPublisher;
    }

    public void notifyOperator(String operatorId, String message) {
        emailSender.send(operatorId, message);
    }

    public void sendComplianceAlert(String conversationId, String reason) {
        pushSender.send("compliance-team", "Alert: " + reason);
        streamBridge.sendToAudit(InternalEvent.create(
                "compliance-alert", "compliance", null, conversationId, null, reason, Map.of()));
    }

    public void broadcastSessionEvent(InternalEvent event) {
        streamBridge.sendToMetrics(event);
        auditPublisher.publishAuditEvent("BROADCAST", event.getConversationId(), Map.of());
    }
}
