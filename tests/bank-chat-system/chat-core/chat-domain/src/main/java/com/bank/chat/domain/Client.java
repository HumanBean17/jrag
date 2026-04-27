package com.bank.chat.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.EnumType;
import javax.persistence.Enumerated;
import javax.persistence.Id;
import javax.persistence.PrePersist;
import javax.persistence.PreUpdate;
import javax.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "client")
public class Client {

    @Id
    @Column(columnDefinition = "uuid", updatable = false, nullable = false)
    private UUID id;

    @Column(name = "conversation_id", nullable = false, unique = true, length = 128)
    private String conversationId;

    @Column(name = "epk_id", nullable = false, length = 128)
    private String epkId;

    @Column(name = "first_name", length = 128)
    private String firstName;

    @Column(name = "last_name", length = 128)
    private String lastName;

    @Enumerated(EnumType.STRING)
    @Column(name = "client_segment", nullable = false, length = 32)
    private ClientSegment clientSegment = ClientSegment.RETAIL;

    @Column(name = "risk_flags", columnDefinition = "TEXT")
    private String riskFlagsJson;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt = Instant.now();

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt = Instant.now();

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
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

    public String getFirstName() {
        return firstName;
    }

    public void setFirstName(String firstName) {
        this.firstName = firstName;
    }

    public String getLastName() {
        return lastName;
    }

    public void setLastName(String lastName) {
        this.lastName = lastName;
    }

    public ClientSegment getClientSegment() {
        return clientSegment;
    }

    public void setClientSegment(ClientSegment clientSegment) {
        this.clientSegment = clientSegment;
    }

    public String getRiskFlagsJson() {
        return riskFlagsJson;
    }

    public void setRiskFlagsJson(String riskFlagsJson) {
        this.riskFlagsJson = riskFlagsJson;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        if (id == null) {
            id = UUID.randomUUID();
        }
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = Instant.now();
    }
}
