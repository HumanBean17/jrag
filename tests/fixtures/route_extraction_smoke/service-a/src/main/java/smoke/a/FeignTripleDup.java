package smoke.a;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

/**
 * Three methods share the same HTTP mapping so they collapse to one Route id with three EXPOSES edges.
 */
@FeignClient(name = "triple-dup", url = "", path = "/dupbase")
public interface FeignTripleDup {
    @GetMapping("/same")
    Object one();

    @GetMapping("/same")
    Object two();

    @GetMapping("/same")
    Object three();
}
