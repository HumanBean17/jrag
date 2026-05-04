package smoke;

/** Distinct arities (#12) and same-arity overload ambiguity (#13). */
public class OverloadPatterns {

    void ovl(int a) {}

    void ovl(int a, int b) {}

    void arity() {
        ovl(1);
        ovl(1, 2);
    }

    void amb(Object o) {}

    void amb(String s) {}

    void sameArity() {
        amb(null);
    }
}
