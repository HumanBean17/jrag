package com.bank.chat.assign.service;

import com.bank.chat.assign.domain.AssignChatEntity;
import com.bank.chat.assign.domain.AssignOperatorSessionEntity;
import com.bank.chat.assign.domain.AssignQueueEntity;
import com.bank.chat.assign.domain.AssignSplitEntity;
import com.bank.chat.assign.integration.ChatCoreJoinClient;
import com.bank.chat.assign.kafka.DistributionTriggerPublisher;
import com.bank.chat.assign.repo.AssignChatRepository;
import com.bank.chat.assign.repo.AssignQueueRepository;
import com.bank.chat.assign.repo.AssignOperatorSessionRepository;
import com.bank.chat.contracts.AssignmentRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.UUID;

@Service
public class ChatManagementService {

    private final AssignChatRepository assignChatRepository;
    private final AssignQueueRepository assignQueueRepository;
    private final AssignOperatorSessionRepository operatorSessionRepository;
    private final SplitResolverService splitResolverService;
    private final DistributionTriggerPublisher distributionTriggerPublisher;
    private final ChatCoreJoinClient chatCoreJoinClient;

    public ChatManagementService(
            AssignChatRepository assignChatRepository,
            AssignQueueRepository assignQueueRepository,
            AssignOperatorSessionRepository operatorSessionRepository,
            SplitResolverService splitResolverService,
            DistributionTriggerPublisher distributionTriggerPublisher,
            ChatCoreJoinClient chatCoreJoinClient
    ) {
        this.assignChatRepository = assignChatRepository;
        this.assignQueueRepository = assignQueueRepository;
        this.operatorSessionRepository = operatorSessionRepository;
        this.splitResolverService = splitResolverService;
        this.distributionTriggerPublisher = distributionTriggerPublisher;
        this.chatCoreJoinClient = chatCoreJoinClient;
    }

    @Transactional
    public void assign(AssignmentRequest request) {
        if (request.getConversationId() == null || request.getConversationId().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "conversationId required");
        }

        AssignSplitEntity split = splitResolverService.resolveSplitName(request.getSplit());
        AssignChatEntity chat = assignChatRepository.findByConversationId(request.getConversationId())
                .orElseGet(() -> {
                    AssignChatEntity c = new AssignChatEntity();
                    c.setId(UUID.randomUUID());
                    c.setConversationId(request.getConversationId());
                    return c;
                });

        chat.setSplit(split);
        chat.setEpkId(request.getEpkId());
        chat.setPriorityScore(request.getPriorityScore());
        chat.setReason(request.getReason());
        assignChatRepository.save(chat);

        if (chat.getOperatorSession() == null) {
            AssignQueueEntity queue = assignQueueRepository.findByAssignChat_Id(chat.getId())
                    .orElseGet(() -> {
                        AssignQueueEntity q = new AssignQueueEntity();
                        q.setId(UUID.randomUUID());
                        q.setAssignChat(chat);
                        return q;
                    });
            queue.setEnqueuedAt(Instant.now());
            queue.setPriorityScore(request.getPriorityScore());
            assignQueueRepository.save(queue);
            distributionTriggerPublisher.publishTrigger();
        }
    }

    @Transactional
    public void closeChat(String conversationId) {
        assignChatRepository.findByConversationId(conversationId)
                .ifPresent(assignChatRepository::delete);
    }

    @Transactional
    public void transfer(String conversationId, String newOperatorId) {
        AssignChatEntity chat = assignChatRepository.findByConversationId(conversationId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "chat not found"));

        AssignOperatorSessionEntity target = operatorSessionRepository
                .findFirstByOperatorIdAndOperatorStatusOrderByCreatedAtDesc(
                        newOperatorId,
                        com.bank.chat.assign.domain.OperatorStatus.AVAILABLE
                )
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "operator session not available"));

        assignQueueRepository.findByAssignChat_Id(chat.getId()).ifPresent(assignQueueRepository::delete);

        chat.setOperatorSession(target);
        assignChatRepository.save(chat);

        chatCoreJoinClient.joinOperator(chat.getConversationId(), target.getOperatorId(), chat.getEpkId());
    }
}
