package smoke.a;

import java.util.function.Function;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class StreamProcessor {
    @Bean
    public Function<String, String> uppercase() {
        return s -> s.toUpperCase();
    }
}
