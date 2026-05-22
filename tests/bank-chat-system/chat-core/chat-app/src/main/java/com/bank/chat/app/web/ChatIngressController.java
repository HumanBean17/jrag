package com.bank.chat.app.web;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InboundAcceptedResponse;
import com.bank.chat.contracts.InboundChatEventRequest;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.ratelimit.ClientMessageRateLimiter;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import com.bank.chat.contracts.brownfield.CodebaseHttpRoute;
import org.springframework.web.bind.annotation.RestController;

import javax.validation.Valid;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/chat")
public class ChatIngressController {

    private final FollowUpKafkaPublisher followUpKafkaPublisher;
    private final ClientMessageRateLimiter rateLimiter;

    public ChatIngressController(
            FollowUpKafkaPublisher followUpKafkaPublisher,
            ClientMessageRateLimiter rateLimiter
    ) {
        this.followUpKafkaPublisher = followUpKafkaPublisher;
        this.rateLimiter = rateLimiter;
    }

    @PostMapping("/events")
    @CodebaseHttpRoute(path = "/api/v1/chat/events", method = "POST")
    public ResponseEntity<InboundAcceptedResponse> accept(
            @Valid @RequestBody InboundChatEventRequest body,
            @RequestHeader(value = "X-Correlation-Id", required = false) String correlationId
    ) {
        if (body.getEventType() == EventType.CLIENT_MESSAGE && body.getEpkId() != null) {
            if (!rateLimiter.allow(body.getEpkId())) {
                return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS).build();
            }
        }

        String corr = correlationId != null ? correlationId : UUID.randomUUID().toString();
        InternalEvent event = InternalEvent.create(
                corr,
                body.getIdempotencyKey(),
                body.getEpkId(),
                body.getConversationId(),
                body.getEventType(),
                body.getMessage(),
                body.getMetadata()
        );
        event.setOperatorId(body.getOperatorId());
        event.setCloserRole(body.getCloserRole());
        event.setSplit(body.getSplit());

        followUpKafkaPublisher.publishIncoming(event);
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(new InboundAcceptedResponse(corr, "ACCEPTED"));
    }
}
