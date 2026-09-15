export class Dispatcher {
    private name: string;

    constructor(name: string) {
        this.name = name;
    }

    public publish(topic: string, payload: any): void {
        // publish(topic, payload) → message_name=topic, receiver=payload
        this.bus.emit(topic, payload);
    //   ^ emit(topic, payload) → message_name=topic, receiver=payload
    }

    public notify(event: string, peer: any): void {
        send(event, peer);
    //   ^ send(event, peer) → message_name=event
    }
}
