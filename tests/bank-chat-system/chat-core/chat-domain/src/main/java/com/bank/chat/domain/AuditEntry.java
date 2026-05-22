package com.bank.chat.domain;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public class AuditEntry {

    private String action;
    private String actor;
    private Map<String, String> details;
    private Instant timestamp;

    public AuditEntry(String action, String actor, Map<String, String> details, Instant timestamp) {
        this.action = action;
        this.actor = actor;
        this.details = details;
        this.timestamp = timestamp;
    }

    public AuditEntry() {
    }

    public String getAction() {
        return action;
    }

    public void setAction(String action) {
        this.action = action;
    }

    public String getActor() {
        return actor;
    }

    public void setActor(String actor) {
        this.actor = actor;
    }

    public Map<String, String> getDetails() {
        return details;
    }

    public void setDetails(Map<String, String> details) {
        this.details = details;
    }

    public Instant getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(Instant timestamp) {
        this.timestamp = timestamp;
    }
}
