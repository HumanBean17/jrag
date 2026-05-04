package smoke;

import java.util.function.Supplier;

/**
 * D1 regression: {@code toString} is not declared on {@link Svc} and no indexed supertype supplies
 * it, so both {@code svc::toString} (extractor arity {@code -1}) and {@code svc.toString()} resolve
 * to the same phantom callee under {@code smoke.Svc}.
 */
public class PhantomMergeD1 {
    void m(Svc svc) {
        Supplier<String> sup = svc::toString;
        String s = svc.toString();
        if (!sup.get().equals(s)) {
            throw new AssertionError();
        }
    }
}
