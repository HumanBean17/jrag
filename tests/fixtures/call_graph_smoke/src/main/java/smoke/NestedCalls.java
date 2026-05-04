package smoke;

import java.util.function.Consumer;

/** Anonymous class (#16), lambda (#11), expression-qualified method ref (#18). */
public class NestedCalls {

    void m() {
        Runnable anon =
                new Runnable() {
                    @Override
                    public void run() {
                        pingFromAnon();
                    }
                };
        Runnable lam = () -> pingFromLambda();
        Consumer<String> expr = getX()::trim;
        use(anon, lam, expr);
    }

    void use(Runnable a, Runnable b, Consumer<String> c) {}

    String getX() {
        return "";
    }

    void pingFromAnon() {}

    void pingFromLambda() {}
}
