package com.bank.chat.contracts;

public enum NotificationChannel {
    EMAIL("email", 1),
    SMS("sms", 2),
    PUSH("push", 3),
    IN_APP("in_app", 4);

    private final String code;
    private final int priority;

    NotificationChannel(String code, int priority) {
        this.code = code;
        this.priority = priority;
    }

    public String getCode() {
        return code;
    }

    public int getPriority() {
        return priority;
    }

    public static NotificationChannel fromCode(String code) {
        if (code == null) {
            return EMAIL;
        }
        for (NotificationChannel channel : values()) {
            if (channel.code.equals(code)) {
                return channel;
            }
        }
        return EMAIL;
    }
}
