package com.example;

import java.util.List;

public class Bus {
    private final String name;

    public Bus(String name) {
        this.name = name;
    }

    public void emitEvent(String topic, Object payload) {
        publish("evt-" + topic, payload, subscriber);
    //   ^ detected: publish("evt-...") → message_name="evt-...", receiver=subscriber
    }

    public void onMessage(String msg, Object data) {
        bus.sendMessage(msg, data);
    //   ^ sendMessage(msg, data) → receiver=msg (unquoted), payload=data
        notify("done", payload, listener);
    //   ^ notify("done", payload, listener) → receiver=listener (third arg)
    }
}
