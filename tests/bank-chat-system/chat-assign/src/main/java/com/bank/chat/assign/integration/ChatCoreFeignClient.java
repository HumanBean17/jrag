package com.bank.chat.assign.integration;

import com.bank.chat.app.web.JoinOperatorRequest;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;

@FeignClient(name = "chat-core", url = "${assign.chat-core.base-url}")
public interface ChatCoreFeignClient {

    @PostMapping("/chat/joinOperator")
    void joinOperator(@RequestBody JoinOperatorRequest request);

    @GetMapping("/api/v1/chat/sessions/{conversationId}")
    SessionInfo getSession(@PathVariable("conversationId") String conversationId);
}
