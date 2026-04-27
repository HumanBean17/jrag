package com.bank.chat.contracts;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class AssignmentRequest {

    private String callbackCorrelationId;
    private String conversationId;
    private String epkId;
    private String clientSegment;
    private String riskFlagsJson;
    private int priorityScore;
    private String reason;
    private String split;
    private boolean afterHoursQueued;
    private Instant deferAssignmentUntil;
    private Instant requestedAt;
    private Map<String, String> hints = new HashMap<>();

    public String getCallbackCorrelationId() {
        return callbackCorrelationId;
    }

    public void setCallbackCorrelationId(String callbackCorrelationId) {
        this.callbackCorrelationId = callbackCorrelationId;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }

    public String getEpkId() {
        return epkId;
    }

    public void setEpkId(String epkId) {
        this.epkId = epkId;
    }

    public String getClientSegment() {
        return clientSegment;
    }

    public void setClientSegment(String clientSegment) {
        this.clientSegment = clientSegment;
    }

    public String getRiskFlagsJson() {
        return riskFlagsJson;
    }

    public void setRiskFlagsJson(String riskFlagsJson) {
        this.riskFlagsJson = riskFlagsJson;
    }

    public int getPriorityScore() {
        return priorityScore;
    }

    public void setPriorityScore(int priorityScore) {
        this.priorityScore = priorityScore;
    }

    public String getReason() {
        return reason;
    }

    public void setReason(String reason) {
        this.reason = reason;
    }

    public String getSplit() {
        return split;
    }

    public void setSplit(String split) {
        this.split = split;
    }

    public boolean isAfterHoursQueued() {
        return afterHoursQueued;
    }

    public void setAfterHoursQueued(boolean afterHoursQueued) {
        this.afterHoursQueued = afterHoursQueued;
    }

    public Instant getDeferAssignmentUntil() {
        return deferAssignmentUntil;
    }

    public void setDeferAssignmentUntil(Instant deferAssignmentUntil) {
        this.deferAssignmentUntil = deferAssignmentUntil;
    }

    public Instant getRequestedAt() {
        return requestedAt;
    }

    public void setRequestedAt(Instant requestedAt) {
        this.requestedAt = requestedAt;
    }

    public Map<String, String> getHints() {
        return hints;
    }

    public void setHints(Map<String, String> hints) {
        this.hints = hints;
    }
}
