package smoke.http;

import org.springframework.web.client.RestTemplate;

public class HttpCallerB {
    private final RestTemplate restTemplate;

    public HttpCallerB(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public String loadAgain() {
        return restTemplate.getForObject("/api/users", String.class);
    }
}
