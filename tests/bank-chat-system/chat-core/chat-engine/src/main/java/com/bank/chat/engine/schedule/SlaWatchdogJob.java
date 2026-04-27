package com.bank.chat.engine.schedule;

import com.bank.chat.domain.ChatSession;
import com.bank.chat.domain.ChatSessionRepository;
import com.bank.chat.domain.SessionStatus;
import com.bank.chat.engine.sla.SlaService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Arrays;
import java.util.List;

@Component
public class SlaWatchdogJob {

    private final ChatSessionRepository chatSessionRepository;
    private final SlaService slaService;

    public SlaWatchdogJob(ChatSessionRepository chatSessionRepository, SlaService slaService) {
        this.chatSessionRepository = chatSessionRepository;
        this.slaService = slaService;
    }

    @Scheduled(fixedDelayString = "${chat.sla-watchdog-ms:15000}")
    @Transactional
    public void sweepBreachedFirstResponse() {
        Instant now = Instant.now();
        List<SessionStatus> statuses = Arrays.asList(
                SessionStatus.AWAITING_ASSIGNMENT,
                SessionStatus.ASSIGNED,
                SessionStatus.ACTIVE,
                SessionStatus.PENDING_CLIENT,
                SessionStatus.PENDING_OPERATOR
        );
        List<ChatSession> sessions = chatSessionRepository.findSessionsWithBreachedFirstResponseSla(now, statuses);
        for (ChatSession session : sessions) {
            slaService.markBreachedAndEscalate(session);
        }
    }
}
