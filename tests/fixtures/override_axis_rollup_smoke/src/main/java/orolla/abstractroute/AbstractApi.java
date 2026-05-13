package orolla.abstractroute;

import org.springframework.web.bind.annotation.RequestMapping;

public abstract class AbstractApi {
    @RequestMapping("/api")
    public abstract void handle();
}
