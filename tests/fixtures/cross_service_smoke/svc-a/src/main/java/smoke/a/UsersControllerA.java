package smoke.a;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UsersControllerA {
    @PostMapping("/api/users")
    public String createUser() {
        return "ok";
    }
}
