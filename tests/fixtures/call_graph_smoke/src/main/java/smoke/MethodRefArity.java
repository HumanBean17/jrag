package smoke;

import java.util.function.Supplier;

/** D2: unambiguous {@code Type::method} ref — CALLS row should carry the resolved method arity. */
public class MethodRefArity {

    static int onlyMethod() {
        return 42;
    }

    void caller() {
        Supplier<Integer> sup = MethodRefArity::onlyMethod;
        if (sup.get() != 42) {
            throw new AssertionError();
        }
    }
}
