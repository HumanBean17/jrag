package com.bank.chat.assign.web;

import com.bank.chat.assign.service.ChatManagementService;
import com.bank.chat.assign.web.dto.ConversationIdBody;
import com.bank.chat.contracts.AssignmentRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.validation.Valid;

@RestController
@RequestMapping("/chat")
public class ChatManagementController {

    private final ChatManagementService chatManagementService;

    public ChatManagementController(ChatManagementService chatManagementService) {
        this.chatManagementService = chatManagementService;
    }

    @PostMapping("/assign")
    public ResponseEntity<Void> assign(@Valid @RequestBody AssignmentRequest body) {
        chatManagementService.assign(body);
        return ResponseEntity.accepted().build();
    }

    @PostMapping("/close")
    public ResponseEntity<Void> close(@Valid @RequestBody ConversationIdBody body) {
        chatManagementService.closeChat(body.getConversationId());
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/transfer/{newOperatorId}")
    public ResponseEntity<Void> transfer(
            @PathVariable String newOperatorId,
            @Valid @RequestBody ConversationIdBody body
    ) {
        chatManagementService.transfer(body.getConversationId(), newOperatorId);
        return ResponseEntity.accepted().build();
    }
}
