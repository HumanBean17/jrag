package com.bank.chat.engine.audit;

import com.bank.chat.domain.AuditEntry;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
@Profile("external-audit")
public class HttpAuditEventPublisher extends AbstractAuditEventPublisher {

    private final RestTemplate auditRestTemplate;

    public HttpAuditEventPublisher(RestTemplate auditRestTemplate) {
        this.auditRestTemplate = auditRestTemplate;
    }

    @Override
    protected void deliver(AuditEntry entry) {
        auditRestTemplate.postForEntity("http://audit-service/api/v1/events", entry, Void.class);
    }
}
