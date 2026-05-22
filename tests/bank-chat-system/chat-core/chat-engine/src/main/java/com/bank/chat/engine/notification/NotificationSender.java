package com.bank.chat.engine.notification;

public interface NotificationSender {
    void send(String recipient, String message);
    String channel();
}
