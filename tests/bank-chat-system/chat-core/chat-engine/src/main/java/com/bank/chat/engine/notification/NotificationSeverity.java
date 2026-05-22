package com.bank.chat.engine.notification;

public enum NotificationSeverity {
    LOW, MEDIUM, HIGH, CRITICAL;

    public boolean requiresImmediateAttention() {
        return this == HIGH || this == CRITICAL;
    }
}
