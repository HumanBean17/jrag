package com.bank.chat.assign.repo;

import com.bank.chat.assign.domain.AssignChatEntity;
import com.bank.chat.assign.domain.AssignOperatorSessionEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface AssignChatRepository extends JpaRepository<AssignChatEntity, UUID> {

    Optional<AssignChatEntity> findByConversationId(String conversationId);

    List<AssignChatEntity> findByOperatorSession(AssignOperatorSessionEntity session);

    long countByOperatorSession(AssignOperatorSessionEntity operatorSession);
}
