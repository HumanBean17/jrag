package smoke;

/** Field / parameter / local-variable receiver shapes (proposal §7.1 #4–6). */
public class ScopeReceivers {

    /** Same simple name as a local below — the local must shadow this field. */
    private String dup;

    private Svc fieldSvc;

    void byField() {
        fieldSvc.work();
    }

    void byParam(Svc p) {
        p.work();
    }

    void byLocal() {
        Svc local = new Svc();
        local.work();
    }

    void shadowLocalOverField() {
        Svc dup = new Svc();
        dup.work();
    }
}
