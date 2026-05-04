package smoke.a;

import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    @RequestMapping(path = {"/a", "/b"}, method = RequestMethod.POST)
    public void dualPaths() {
    }
}
