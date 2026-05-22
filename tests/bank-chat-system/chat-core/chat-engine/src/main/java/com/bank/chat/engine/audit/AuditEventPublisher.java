package com.bank.chat.engine.audit;

import java.util.Map;

public interface AuditEventPublisher {
    void publishAuditEvent(String action, String actor, Map<String, String> details);
}
