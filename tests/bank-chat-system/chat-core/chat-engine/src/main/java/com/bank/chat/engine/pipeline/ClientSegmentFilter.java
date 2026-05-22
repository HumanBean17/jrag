package com.bank.chat.engine.pipeline;

import com.bank.chat.domain.Client;
import org.springframework.stereotype.Component;

@Component
public class ClientSegmentFilter implements EventFilter<Client> {

    @Override
    public boolean test(Client input) {
        return input.getClientSegment() != null;
    }

    @Override
    public String name() {
        return "clientSegmentFilter";
    }
}
