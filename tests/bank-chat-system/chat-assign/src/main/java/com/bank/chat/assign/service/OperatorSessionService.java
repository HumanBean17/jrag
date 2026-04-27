package com.bank.chat.assign.service;

import com.bank.chat.assign.domain.AssignChatEntity;
import com.bank.chat.assign.domain.AssignOperatorSessionEntity;
import com.bank.chat.assign.domain.AssignOperatorSplitEntity;
import com.bank.chat.assign.domain.AssignQueueEntity;
import com.bank.chat.assign.domain.AssignSplitEntity;
import com.bank.chat.assign.domain.OperatorStatus;
import com.bank.chat.assign.kafka.DistributionTriggerPublisher;
import com.bank.chat.assign.repo.AssignChatRepository;
import com.bank.chat.assign.repo.AssignOperatorSessionRepository;
import com.bank.chat.assign.repo.AssignOperatorSplitRepository;
import com.bank.chat.assign.repo.AssignQueueRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Service
public class OperatorSessionService {

    private final AssignOperatorSessionRepository operatorSessionRepository;
    private final AssignOperatorSplitRepository operatorSplitRepository;
    private final AssignChatRepository assignChatRepository;
    private final AssignQueueRepository assignQueueRepository;
    private final SplitResolverService splitResolverService;
    private final DistributionTriggerPublisher distributionTriggerPublisher;

    public OperatorSessionService(
            AssignOperatorSessionRepository operatorSessionRepository,
            AssignOperatorSplitRepository operatorSplitRepository,
            AssignChatRepository assignChatRepository,
            AssignQueueRepository assignQueueRepository,
            SplitResolverService splitResolverService,
            DistributionTriggerPublisher distributionTriggerPublisher
    ) {
        this.operatorSessionRepository = operatorSessionRepository;
        this.operatorSplitRepository = operatorSplitRepository;
        this.assignChatRepository = assignChatRepository;
        this.assignQueueRepository = assignQueueRepository;
        this.splitResolverService = splitResolverService;
        this.distributionTriggerPublisher = distributionTriggerPublisher;
    }

    @Transactional
    public UUID openSession(String operatorId, List<String> splitNames) {
        AssignOperatorSessionEntity session = new AssignOperatorSessionEntity();
        session.setId(UUID.randomUUID());
        session.setOperatorId(operatorId);
        session.setOperatorStatus(OperatorStatus.AVAILABLE);
        operatorSessionRepository.save(session);

        List<String> names = (splitNames == null || splitNames.isEmpty())
                ? List.of("general")
                : splitNames;

        for (String raw : names) {
            AssignSplitEntity split = splitResolverService.resolveSplitName(raw);
            AssignOperatorSplitEntity link = new AssignOperatorSplitEntity();
            link.setOperatorSessionId(session.getId());
            link.setSplitId(split.getId());
            link.setOperatorSession(session);
            link.setSplit(split);
            operatorSplitRepository.save(link);
        }

        distributionTriggerPublisher.publishTrigger();
        return session.getId();
    }

    @Transactional
    public void closeSession(UUID sessionId) {
        AssignOperatorSessionEntity session = operatorSessionRepository.findById(sessionId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "session not found"));

        List<AssignChatEntity> chats = new ArrayList<>(assignChatRepository.findByOperatorSession(session));
        for (AssignChatEntity chat : chats) {
            chat.setOperatorSession(null);
            assignChatRepository.save(chat);
            if (!assignQueueRepository.existsByAssignChat_Id(chat.getId())) {
                AssignQueueEntity q = new AssignQueueEntity();
                q.setId(UUID.randomUUID());
                q.setAssignChat(chat);
                q.setEnqueuedAt(Instant.now());
                q.setPriorityScore(chat.getPriorityScore());
                assignQueueRepository.save(q);
            }
        }

        operatorSplitRepository.deleteAllByOperatorSessionId(session.getId());
        operatorSessionRepository.delete(session);
        distributionTriggerPublisher.publishTrigger();
    }

    @Transactional
    public void updateStatus(UUID sessionId, String newStatusRaw) {
        OperatorStatus newStatus;
        try {
            newStatus = OperatorStatus.valueOf(newStatusRaw.trim().toUpperCase());
        } catch (IllegalArgumentException ex) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid status");
        }

        AssignOperatorSessionEntity session = operatorSessionRepository.findById(sessionId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "session not found"));

        session.setOperatorStatus(newStatus);
        operatorSessionRepository.save(session);

        if (newStatus == OperatorStatus.AVAILABLE) {
            distributionTriggerPublisher.publishTrigger();
        }
    }
}
