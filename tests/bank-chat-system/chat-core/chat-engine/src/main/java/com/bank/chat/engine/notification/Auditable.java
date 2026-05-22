package com.bank.chat.engine.notification;

public interface Auditable extends Trackable {
    void recordAudit(String action);
}
