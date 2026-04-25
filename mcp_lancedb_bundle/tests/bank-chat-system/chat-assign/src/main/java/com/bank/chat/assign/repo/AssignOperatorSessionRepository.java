package com.bank.chat.assign.repo;

import com.bank.chat.assign.domain.AssignOperatorSessionEntity;
import com.bank.chat.assign.domain.OperatorStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface AssignOperatorSessionRepository extends JpaRepository<AssignOperatorSessionEntity, UUID> {

    Optional<AssignOperatorSessionEntity> findFirstByOperatorIdAndOperatorStatusOrderByCreatedAtDesc(
            String operatorId,
            OperatorStatus status
    );

    @Query("SELECT o FROM AssignOperatorSessionEntity o WHERE o.operatorStatus = :status "
            + "AND o.id IN (SELECT aos.operatorSessionId FROM AssignOperatorSplitEntity aos WHERE aos.splitId = :splitId) "
            + "ORDER BY o.id")
    List<AssignOperatorSessionEntity> findAvailableForSplit(
            @Param("splitId") UUID splitId,
            @Param("status") OperatorStatus status
    );
}
