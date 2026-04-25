package com.bank.chat.app.web;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.config.ChatEngineProperties;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.validation.Valid;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/chat")
public class JoinOperatorController {

    public static final String INTERNAL_TOKEN_HEADER = "X-Chat-Internal-Token";

    private final FollowUpKafkaPublisher followUpKafkaPublisher;
    private final ChatEngineProperties chatEngineProperties;

    public JoinOperatorController(
            FollowUpKafkaPublisher followUpKafkaPublisher,
            ChatEngineProperties chatEngineProperties
    ) {
        this.followUpKafkaPublisher = followUpKafkaPublisher;
        this.chatEngineProperties = chatEngineProperties;
    }

    @PostMapping("/joinOperator")
    public ResponseEntity<Void> joinOperator(
            @Valid @RequestBody JoinOperatorRequest body,
            @RequestHeader(value = INTERNAL_TOKEN_HEADER, required = false) String internalToken
    ) {
        String expected = chatEngineProperties.getJoinOperator().getInternalToken();
        if (StringUtils.hasText(expected) && !expected.equals(internalToken)) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
        }

        String corr = StringUtils.hasText(body.getCorrelationId())
                ? body.getCorrelationId()
                : UUID.randomUUID().toString();
        String idem = StringUtils.hasText(body.getIdempotencyKey())
                ? body.getIdempotencyKey()
                : "join-" + body.getConversationId() + "-" + body.getOperatorId();
        String epk = StringUtils.hasText(body.getEpkId()) ? body.getEpkId() : "unknown";

        Map<String, String> md = new HashMap<>();
        md.put("source", "chat-assign");

        InternalEvent event = InternalEvent.create(
                corr,
                idem,
                epk,
                body.getConversationId(),
                EventType.OPERATOR_ASSIGNED,
                body.getOperatorId(),
                md
        );
        event.setOperatorId(body.getOperatorId());

        followUpKafkaPublisher.publishIncoming(event);
        return ResponseEntity.accepted().build();
    }
}
