package com.bank.chat.assign.web;

import com.bank.chat.assign.service.OperatorSessionService;
import com.bank.chat.assign.web.dto.OpenSessionBody;
import com.bank.chat.assign.web.dto.OpenSessionResponse;
import com.bank.chat.assign.web.dto.SessionIdBody;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.validation.Valid;

@RestController
@RequestMapping("/session")
public class OperatorManagementController {

    private final OperatorSessionService operatorSessionService;

    public OperatorManagementController(OperatorSessionService operatorSessionService) {
        this.operatorSessionService = operatorSessionService;
    }

    @PostMapping("/open")
    public ResponseEntity<OpenSessionResponse> open(@Valid @RequestBody OpenSessionBody body) {
        return ResponseEntity.ok(new OpenSessionResponse(
                operatorSessionService.openSession(body.getOperatorId(), body.getSplitNames())
        ));
    }

    @PostMapping("/close")
    public ResponseEntity<Void> close(@Valid @RequestBody SessionIdBody body) {
        operatorSessionService.closeSession(body.getSessionId());
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/status/{newStatus}")
    public ResponseEntity<Void> status(
            @PathVariable String newStatus,
            @Valid @RequestBody SessionIdBody body
    ) {
        operatorSessionService.updateStatus(body.getSessionId(), newStatus);
        return ResponseEntity.accepted().build();
    }
}
