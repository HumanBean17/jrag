package com.bank.chat.assign.repo;

import com.bank.chat.assign.domain.AssignQueueEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;

import javax.persistence.LockModeType;
import java.util.Optional;
import java.util.UUID;

public interface AssignQueueRepository extends JpaRepository<AssignQueueEntity, UUID> {

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    Optional<AssignQueueEntity> findFirstByOrderByPriorityScoreDescEnqueuedAtAsc();

    Optional<AssignQueueEntity> findByAssignChat_Id(UUID assignChatId);

    boolean existsByAssignChat_Id(UUID assignChatId);
}
