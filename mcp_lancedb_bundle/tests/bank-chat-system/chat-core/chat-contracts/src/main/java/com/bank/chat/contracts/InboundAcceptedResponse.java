package com.bank.chat.contracts;

public class InboundAcceptedResponse {

    private String correlationId;
    private String status;

    public InboundAcceptedResponse() {
    }

    public InboundAcceptedResponse(String correlationId, String status) {
        this.correlationId = correlationId;
        this.status = status;
    }

    public String getCorrelationId() {
        return correlationId;
    }

    public void setCorrelationId(String correlationId) {
        this.correlationId = correlationId;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }
}
