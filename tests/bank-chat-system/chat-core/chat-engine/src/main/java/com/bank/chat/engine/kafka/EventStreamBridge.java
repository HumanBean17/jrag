package com.bank.chat.engine.kafka;

import com.bank.chat.contracts.InternalEvent;
import com.bank.chat.contracts.brownfield.CodebaseProducer;
import com.bank.chat.contracts.brownfield.CodebaseProducers;
import org.springframework.cloud.stream.function.StreamBridge;
import org.springframework.stereotype.Component;

@Component
public class EventStreamBridge {

    private final StreamBridge streamBridge;

    public EventStreamBridge(StreamBridge streamBridge) {
        this.streamBridge = streamBridge;
    }

    @CodebaseProducers({
        @CodebaseProducer(topic = "banking.chat.audit", producerKind = "stream_bridge_send"),
        @CodebaseProducer(topic = "banking.chat.audit.dlq", producerKind = "kafka_send")
    })
    public void sendToAudit(InternalEvent event) {
        streamBridge.send("banking.chat.audit-out", event);
    }

    public void sendToMetrics(InternalEvent event) {
        streamBridge.send("banking.chat.metrics-out", event);
    }
}
