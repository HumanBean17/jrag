package com.bank.chat.assign.repo;

import com.bank.chat.assign.domain.AssignOperatorSplitEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface AssignOperatorSplitRepository extends JpaRepository<AssignOperatorSplitEntity, AssignOperatorSplitEntity.Key> {

    void deleteAllByOperatorSessionId(UUID operatorSessionId);
}
