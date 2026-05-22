package com.bank.chat.engine.integration;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class CrmClient {

    private final RestTemplate crmRestTemplate;

    public CrmClient(RestTemplate crmRestTemplate) {
        this.crmRestTemplate = crmRestTemplate;
    }

    public CustomerProfile fetchProfile(String epkId) {
        return crmRestTemplate.getForObject("http://crm-service/api/v1/customers/" + epkId, CustomerProfile.class);
    }
}
