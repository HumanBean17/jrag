package com.bank.chat.engine.ingest;

import com.bank.chat.contracts.ChatTopics;
import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.contracts.brownfield.CodebaseAsyncRoute;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class ComplianceReviewListener {

    @KafkaListener(topics = ChatTopics.COMPLIANCE_REVIEW)
    @CodebaseAsyncRoute(topic = "banking.chat.compliance.review", framework = "kafka")
    public void onComplianceReview(InternalEvent event) {
    }
}
