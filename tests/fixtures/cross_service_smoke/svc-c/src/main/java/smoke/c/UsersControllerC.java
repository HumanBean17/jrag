package smoke.c;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UsersControllerC {
    @PostMapping("/api/users")
    public String createUser() {
        return "ok";
    }
}
