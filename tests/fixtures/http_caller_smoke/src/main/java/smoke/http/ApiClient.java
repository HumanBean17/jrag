package smoke.http;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

@FeignClient(name = "user-svc")
public interface ApiClient {
    @GetMapping("/api/users")
    String loadUsers();
}
