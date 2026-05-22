package com.bank.chat.engine.notification;

import org.springframework.stereotype.Component;

@Component
public class PushNotificationSender extends AbstractNotificationSender {

    private final PushGatewayClient pushGatewayClient;

    public PushNotificationSender(PushGatewayClient pushGatewayClient) {
        this.pushGatewayClient = pushGatewayClient;
    }

    @Override
    public String channel() {
        return "PUSH";
    }

    @Override
    protected void doSend(String recipient, String payload) {
        pushGatewayClient.sendPush(recipient, payload);
    }
}
