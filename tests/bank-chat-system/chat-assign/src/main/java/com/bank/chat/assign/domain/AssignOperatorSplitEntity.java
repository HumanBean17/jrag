package com.bank.chat.assign.domain;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.FetchType;
import javax.persistence.Id;
import javax.persistence.IdClass;
import javax.persistence.JoinColumn;
import javax.persistence.ManyToOne;
import javax.persistence.Table;
import java.io.Serializable;
import java.util.Objects;
import java.util.UUID;

@Entity
@Table(name = "assign_operator_split")
@IdClass(AssignOperatorSplitEntity.Key.class)
public class AssignOperatorSplitEntity {

    @Id
    @Column(name = "operator_session_id", nullable = false)
    private UUID operatorSessionId;

    @Id
    @Column(name = "split_id", nullable = false)
    private UUID splitId;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "operator_session_id", insertable = false, updatable = false)
    private AssignOperatorSessionEntity operatorSession;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "split_id", insertable = false, updatable = false)
    private AssignSplitEntity split;

    public UUID getOperatorSessionId() {
        return operatorSessionId;
    }

    public void setOperatorSessionId(UUID operatorSessionId) {
        this.operatorSessionId = operatorSessionId;
    }

    public UUID getSplitId() {
        return splitId;
    }

    public void setSplitId(UUID splitId) {
        this.splitId = splitId;
    }

    public AssignOperatorSessionEntity getOperatorSession() {
        return operatorSession;
    }

    public void setOperatorSession(AssignOperatorSessionEntity operatorSession) {
        this.operatorSession = operatorSession;
    }

    public AssignSplitEntity getSplit() {
        return split;
    }

    public void setSplit(AssignSplitEntity split) {
        this.split = split;
    }

    public static class Key implements Serializable {

        private UUID operatorSessionId;
        private UUID splitId;

        public Key() {
        }

        public Key(UUID operatorSessionId, UUID splitId) {
            this.operatorSessionId = operatorSessionId;
            this.splitId = splitId;
        }

        public UUID getOperatorSessionId() {
            return operatorSessionId;
        }

        public void setOperatorSessionId(UUID operatorSessionId) {
            this.operatorSessionId = operatorSessionId;
        }

        public UUID getSplitId() {
            return splitId;
        }

        public void setSplitId(UUID splitId) {
            this.splitId = splitId;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) {
                return true;
            }
            if (o == null || getClass() != o.getClass()) {
                return false;
            }
            Key key = (Key) o;
            return Objects.equals(operatorSessionId, key.operatorSessionId)
                    && Objects.equals(splitId, key.splitId);
        }

        @Override
        public int hashCode() {
            return Objects.hash(operatorSessionId, splitId);
        }
    }
}
