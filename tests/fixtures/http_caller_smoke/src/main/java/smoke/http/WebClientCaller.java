package smoke.http;

import org.springframework.web.reactive.function.client.WebClient;

public class WebClientCaller {
    private final WebClient webClient;

    public WebClientCaller(WebClient webClient) {
        this.webClient = webClient;
    }

    public void go() {
        webClient.get().uri("/x").retrieve();
    }
}
