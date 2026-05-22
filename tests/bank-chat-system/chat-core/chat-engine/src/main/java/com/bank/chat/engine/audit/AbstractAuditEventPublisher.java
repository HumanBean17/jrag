package com.bank.chat.engine.audit;

import com.bank.chat.domain.AuditEntry;

import java.time.Instant;
import java.util.Map;

public abstract class AbstractAuditEventPublisher implements AuditEventPublisher {

    @Override
    public void publishAuditEvent(String action, String actor, Map<String, String> details) {
        AuditEntry entry = buildEntry(action, actor, details);
        deliver(entry);
    }

    protected abstract void deliver(AuditEntry entry);

    protected AuditEntry buildEntry(String action, String actor, Map<String, String> details) {
        return new AuditEntry(action, actor, details, Instant.now());
    }

    public static String formatAuditKey(String action, Instant when) {
        return action + ":" + when.toString();
    }
}
