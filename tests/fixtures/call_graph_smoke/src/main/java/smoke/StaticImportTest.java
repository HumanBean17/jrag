package smoke;

import static java.util.Objects.requireNonNull;

/** Minimal fixture for static-import call extraction. */
public class StaticImportTest {

    void m() {
        requireNonNull("x");
    }
}
