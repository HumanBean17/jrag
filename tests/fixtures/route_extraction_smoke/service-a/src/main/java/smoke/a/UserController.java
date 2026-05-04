package smoke.a;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UserController {
    /** Same literal path as smoke.b.UserController — Route ids must still differ by microservice. */
    @GetMapping("/api/users")
    public String users() {
        return "";
    }
}
