package com.bank.chat.engine.notification;

public abstract class AbstractNotificationSender implements NotificationSender {
    @Override
    public final void send(String recipient, String message) {
        validate(recipient, message);
        String enriched = enrich(message);
        doSend(recipient, enriched);
        audit(recipient, enriched);
    }

    protected abstract void doSend(String recipient, String payload);

    protected void validate(String recipient, String message) {
    }

    protected String enrich(String message) {
        return message;
    }

    protected void audit(String recipient, String payload) {
    }
}
