package com.foo

import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.GetMapping

/**
 * Kotlin controller that constructor-injects the Java [UserService] and calls it.
 * Spring ``@RestController`` must map to role CONTROLLER (detector reused on Kotlin).
 */
@RestController
class UserController(val userService: UserService) {

    @GetMapping("/users")
    fun get(): String {
        return userService.getById(1L)
    }
}
