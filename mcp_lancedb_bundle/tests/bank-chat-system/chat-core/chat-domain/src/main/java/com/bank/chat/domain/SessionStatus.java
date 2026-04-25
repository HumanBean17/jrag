package com.bank.chat.domain;

public enum SessionStatus {
    INITIAL,
    AWAITING_ASSIGNMENT,
    ASSIGNED,
    ACTIVE,
    PENDING_CLIENT,
    PENDING_OPERATOR,
    ON_HOLD,
    CLOSING,
    CLOSED
}
