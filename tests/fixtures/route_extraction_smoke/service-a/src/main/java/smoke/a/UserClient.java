package smoke.a;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@FeignClient(name = "user-svc", url = "", path = "/users")
public interface UserClient {
    @GetMapping("/{id}")
    Object get(@PathVariable("id") String id);

    @GetMapping("/list")
    Object listAll();

    @GetMapping("/health")
    Object health();
}
