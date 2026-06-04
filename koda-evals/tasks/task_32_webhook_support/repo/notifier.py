class Notifier:
    def __init__(self):
        self.webhook_url = None

    def notify(self, message):
        print(f"[console] {message}")
