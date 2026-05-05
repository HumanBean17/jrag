package smoke.http;

import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.web.client.RestTemplate;

public class HttpExchangeCaller {
    private final RestTemplate restTemplate;

    public HttpExchangeCaller(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public void call() {
        restTemplate.exchange("/api/users", HttpMethod.PUT, HttpEntity.EMPTY, String.class);
    }
}
