package com.bank.chat.app.web.dev;

import com.bank.chat.contracts.EventType;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.engine.kafka.FollowUpKafkaPublisher;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@RestController
@Profile("dev")
@RequestMapping("/api/dev")
public class DevAssignmentController {

    private final FollowUpKafkaPublisher publisher;

    public DevAssignmentController(FollowUpKafkaPublisher publisher) {
        this.publisher = publisher;
    }

    @PostMapping("/conversations/{conversationId}/assign/{operatorId}")
    public ResponseEntity<Void> simulateAssign(
            @PathVariable String conversationId,
            @PathVariable String operatorId
    ) {
        Map<String, String> md = new HashMap<>();
        md.put("source", "dev-controller");
        InternalEvent event = InternalEvent.create(
                UUID.randomUUID().toString(),
                "dev-assign-" + conversationId,
                "dev-epk",
                conversationId,
                EventType.OPERATOR_ASSIGNED,
                operatorId,
                md
        );
        event.setOperatorId(operatorId);
        publisher.publishIncoming(event);
        return ResponseEntity.accepted().build();
    }
}
