package com.bank.chat.assign.domain;

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
@Table(name = "assign_operator_session")
public class AssignOperatorSessionEntity {

    @Id
    @Column(columnDefinition = "uuid", nullable = false)
    private UUID id;

    @Column(name = "operator_id", nullable = false, length = 128)
    private String operatorId;

    @Enumerated(EnumType.STRING)
    @Column(name = "operator_status", nullable = false, length = 32)
    private OperatorStatus operatorStatus = OperatorStatus.AVAILABLE;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @PrePersist
    void prePersist() {
        Instant now = Instant.now();
        createdAt = now;
        updatedAt = now;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = Instant.now();
    }

    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public String getOperatorId() {
        return operatorId;
    }

    public void setOperatorId(String operatorId) {
        this.operatorId = operatorId;
    }

    public OperatorStatus getOperatorStatus() {
        return operatorStatus;
    }

    public void setOperatorStatus(OperatorStatus operatorStatus) {
        this.operatorStatus = operatorStatus;
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
}
