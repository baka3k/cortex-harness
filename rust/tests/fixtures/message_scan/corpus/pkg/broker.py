class Broker:
    def __init__(self, name):
        self.name = name

    def send_msg(self, msg, target):
        # send_msg(msg, target) → message_name=msg, payload=target
        self.publish(msg, target)
    #   ^ publish(msg, target) → message_name=msg, payload=target

    def broadcast(self, payload, peer):
        return emit(payload, peer)
    #   ^ emit(payload, peer) → message_name=payload
