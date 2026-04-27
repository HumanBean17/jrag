package com.bank.chat.contracts;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

public enum EventType {
    CLIENT_MESSAGE,
    OPERATOR_MESSAGE,
    CLIENT_READ_MESSAGE,
    OPERATOR_READ_MESSAGE,
    OPERATOR_ASSIGNED,
    OPERATOR_TRANSFER_REQUESTED,
    OPERATOR_TRANSFER_COMPLETED,
    ACK,
    CLOSE_CHAT,
    CLOSE_CHAT_FINALIZE,
    COMPLIANCE_HOLD,
    COMPLIANCE_RELEASE,
    SESSION_REOPEN,
    OPERATOR_TYPING,
    SYSTEM_REJECTED_TRANSITION,
    SLA_BREACHED,
    ESCALATION_REQUESTED,
    ASSIGNMENT_DEFERRED,
    CLIENT_THROTTLED,
    COMPLIANCE_FLAGGED,
    READ_RECEIPT,
    ASSIGNMENT_COMPLETED;

    @JsonValue
    public String toJson() {
        return name();
    }

    @JsonCreator
    public static EventType fromJson(String value) {
        if (value == null) {
            return null;
        }
        return EventType.valueOf(value.trim());
    }
}
