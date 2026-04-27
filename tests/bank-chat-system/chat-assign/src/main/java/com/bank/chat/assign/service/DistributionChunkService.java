package com.bank.chat.assign.service;

import com.bank.chat.assign.config.AssignProperties;
import com.bank.chat.assign.domain.AssignChatEntity;
import com.bank.chat.assign.domain.AssignOperatorSessionEntity;
import com.bank.chat.assign.domain.AssignQueueEntity;
import com.bank.chat.assign.domain.OperatorStatus;
import com.bank.chat.assign.integration.ChatCoreJoinClient;
import com.bank.chat.assign.repo.AssignChatRepository;
import com.bank.chat.assign.repo.AssignOperatorSessionRepository;
import com.bank.chat.assign.repo.AssignQueueRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
public class DistributionChunkService {

    private static final Logger log = LoggerFactory.getLogger(DistributionChunkService.class);

    private final AssignQueueRepository queueRepository;
    private final AssignChatRepository chatRepository;
    private final AssignOperatorSessionRepository operatorSessionRepository;
    private final ChatCoreJoinClient chatCoreJoinClient;
    private final AssignProperties assignProperties;

    public DistributionChunkService(
            AssignQueueRepository queueRepository,
            AssignChatRepository chatRepository,
            AssignOperatorSessionRepository operatorSessionRepository,
            ChatCoreJoinClient chatCoreJoinClient,
            AssignProperties assignProperties
    ) {
        this.queueRepository = queueRepository;
        this.chatRepository = chatRepository;
        this.operatorSessionRepository = operatorSessionRepository;
        this.chatCoreJoinClient = chatCoreJoinClient;
        this.assignProperties = assignProperties;
    }

    /**
     * One queue item per transaction so Kafka redelivery does not hold a long DB transaction.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public boolean tryAssignNextFromQueue() {
        Optional<AssignQueueEntity> head = queueRepository.findFirstByOrderByPriorityScoreDescEnqueuedAtAsc();
        if (head.isEmpty()) {
            return false;
        }

        AssignQueueEntity queueRow = head.get();
        UUID chatId = queueRow.getAssignChat().getId();
        AssignChatEntity chat = chatRepository.findById(chatId).orElseThrow();

        if (chat.getOperatorSession() != null) {
            queueRepository.delete(queueRow);
            return true;
        }

        Optional<AssignOperatorSessionEntity> operator = pickEligibleOperator(chat.getSplit().getId());
        if (operator.isEmpty()) {
            return false;
        }

        AssignOperatorSessionEntity op = operator.get();
        try {
            chatCoreJoinClient.joinOperator(
                    chat.getConversationId(),
                    op.getOperatorId(),
                    chat.getEpkId()
            );
        } catch (Exception ex) {
            log.debug("joinOperator deferred, will retry on next trigger: {}", ex.toString());
            return false;
        }

        chat.setOperatorSession(op);
        chatRepository.save(chat);
        queueRepository.delete(queueRow);
        return true;
    }

    private Optional<AssignOperatorSessionEntity> pickEligibleOperator(UUID splitId) {
        int max = assignProperties.getDistribution().getMaxChatsPerOperator();
        List<AssignOperatorSessionEntity> candidates = operatorSessionRepository.findAvailableForSplit(
                splitId,
                OperatorStatus.AVAILABLE
        );
        for (AssignOperatorSessionEntity o : candidates) {
            long n = chatRepository.countByOperatorSession(o);
            if (n < max) {
                return Optional.of(o);
            }
        }
        return Optional.empty();
    }
}
