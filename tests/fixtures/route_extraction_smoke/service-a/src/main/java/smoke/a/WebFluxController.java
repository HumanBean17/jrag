package smoke.a;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Mono;

@RestController
public class WebFluxController {
    @GetMapping("/flux/hello")
    public Mono<String> hello() {
        return Mono.empty();
    }
}
