package smoke.http;

import org.springframework.cloud.stream.function.StreamBridge;

public class StreamBridgeCaller {
    private final StreamBridge streamBridge;

    public StreamBridgeCaller(StreamBridge streamBridge) {
        this.streamBridge = streamBridge;
    }

    public void go(Object payload) {
        streamBridge.send("binding-out-0", payload);
    }
}
