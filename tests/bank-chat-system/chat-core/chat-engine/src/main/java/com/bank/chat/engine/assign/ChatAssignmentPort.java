package com.bank.chat.engine.assign;

import com.bank.chat.contracts.AssignmentRequest;

public interface ChatAssignmentPort {

    void requestAssignment(AssignmentRequest request);
}
