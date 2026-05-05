package smoke.http;

import org.springframework.web.client.RestTemplate;

public class HttpCallerA {
    private final RestTemplate restTemplate;

    public HttpCallerA(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public String load() {
        return restTemplate.getForObject("/api/users", String.class);
    }
}
