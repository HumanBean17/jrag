package smoke;

/** Subclass using {@code super.} prefix on the same field chain (D6 smoke). */
public class FieldChainSub extends FieldChainBase {

    void bySuperChain() {
        super.root.mid.inner.target();
    }
}
