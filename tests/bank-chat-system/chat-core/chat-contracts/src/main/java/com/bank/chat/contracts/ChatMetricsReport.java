package com.bank.chat.contracts;

public record ChatMetricsReport(
    long totalSessions,
    long activeSessions,
    long breachedSlaCount,
    double avgFirstResponseSeconds
) {}
