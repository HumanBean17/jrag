package com.foo;

/**
 * Java caller of a Kotlin synthesized JVM accessor.
 *
 * ``UserDto`` is a Kotlin data class; ``getName()`` is the JVM getter Kotlin
 * synthesizes for ``val name``. This proves the cross-language CALLS edge from
 * Java to the synthesized Kotlin accessor (B1 parity).
 */
public class UserDtoPrinter {
    public String render(UserDto dto) {
        return dto.getName();
    }
}
