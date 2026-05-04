package smoke;

/**
 * D6: exercises {@code _resolve_this_super_field_chain} — {@code this.a.b.c.m()} with only
 * field segments (no calls in the receiver).
 */
public class FieldChainReceivers {

    private FieldChainOuter root;

    void byThisChain() {
        this.root.mid.inner.target();
    }
}
