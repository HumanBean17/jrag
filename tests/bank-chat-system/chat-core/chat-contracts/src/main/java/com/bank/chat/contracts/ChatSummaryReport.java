package com.bank.chat.contracts;

import java.util.List;
import java.util.Map;

public record ChatSummaryReport(
    String period,
    List<SessionSummary> sessions,
    Map<String, Long> bySegment
) {}
