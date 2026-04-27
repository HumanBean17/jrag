package com.bank.chat.contracts;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class InternalEvent {

    private String correlationId;
    private String idempotencyKey;
    private String epkId;
    private String conversationId;
    private EventType eventType;
    private String message;
    private String operatorId;
    private String closerRole;
    private String split;
    private Map<String, String> metadata = new HashMap<>();
    private Instant occurredAt;

    public static InternalEvent create(
            String correlationId,
            String idempotencyKey,
            String epkId,
            String conversationId,
            EventType eventType,
            String message,
            Map<String, String> metadata
    ) {
        InternalEvent e = new InternalEvent();
        e.setCorrelationId(correlationId != null ? correlationId : UUID.randomUUID().toString());
        e.setIdempotencyKey(idempotencyKey != null ? idempotencyKey : e.getCorrelationId());
        e.setEpkId(epkId);
        e.setConversationId(conversationId);
        e.setEventType(eventType);
        e.setMessage(message);
        e.setMetadata(metadata != null ? new HashMap<>(metadata) : new HashMap<>());
        e.setOccurredAt(Instant.now());
        return e;
    }

    public String getCorrelationId() {
        return correlationId;
    }

    public void setCorrelationId(String correlationId) {
        this.correlationId = correlationId;
    }

    public String getIdempotencyKey() {
        return idempotencyKey;
    }

    public void setIdempotencyKey(String idempotencyKey) {
        this.idempotencyKey = idempotencyKey;
    }

    public String getEpkId() {
        return epkId;
    }

    public void setEpkId(String epkId) {
        this.epkId = epkId;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public EventType getEventType() {
        return eventType;
    }

    public void setEventType(EventType eventType) {
        this.eventType = eventType;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public String getOperatorId() {
        return operatorId;
    }

    public void setOperatorId(String operatorId) {
        this.operatorId = operatorId;
    }

    public String getCloserRole() {
        return closerRole;
    }

    public void setCloserRole(String closerRole) {
        this.closerRole = closerRole;
    }

    public String getSplit() {
        return split;
    }

    public void setSplit(String split) {
        this.split = split;
    }

    public Map<String, String> getMetadata() {
        return metadata;
    }

    public void setMetadata(Map<String, String> metadata) {
        this.metadata = metadata;
    }

    public Instant getOccurredAt() {
        return occurredAt;
    }

    public void setOccurredAt(Instant occurredAt) {
        this.occurredAt = occurredAt;
    }
}
