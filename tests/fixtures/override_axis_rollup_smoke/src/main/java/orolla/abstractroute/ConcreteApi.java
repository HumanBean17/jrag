package orolla.abstractroute;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ConcreteApi extends AbstractApi {
    @Override
    @PostMapping("/do")
    public void handle() {}
}
