package com.bank.chat.engine.processors;

import com.bank.chat.contracts.EventType;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.annotation.AnnotationAwareOrderComparator;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Component
public class EventProcessorRegistry {

    private final List<EventProcessor> processors;

    @Autowired
    public EventProcessorRegistry(List<EventProcessor> processors) {
        List<EventProcessor> copy = new ArrayList<>(processors);
        copy.sort(AnnotationAwareOrderComparator.INSTANCE);
        this.processors = copy;
    }

    public Optional<EventProcessor> find(EventType type) {
        return processors.stream().filter(p -> p.supports(type)).findFirst();
    }
}
