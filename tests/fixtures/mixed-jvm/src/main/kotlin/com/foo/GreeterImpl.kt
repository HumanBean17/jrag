package com.foo

/**
 * Kotlin class implementing the Java [Greeter] interface.
 * The extractor routes an unknown (cross-file) supertype to IMPLEMENTS, and the
 * index resolves it to ``com.foo.Greeter``.
 */
class GreeterImpl : Greeter {

    override fun greet(): String {
        return "hi"
    }
}
