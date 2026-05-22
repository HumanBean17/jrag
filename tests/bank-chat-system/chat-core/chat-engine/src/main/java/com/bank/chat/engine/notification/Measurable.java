package com.bank.chat.engine.notification;

public interface Measurable extends Trackable {
    long getDeliveryLatencyMs();
}
