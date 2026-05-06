class App:
    def __init__(self):
        self.routes = {}

    def register_route(self, path, handler):
        self.routes[path] = handler

    def handle(self, path):
        handler = self.routes.get(path)
        if handler is None:
            return "404"
        return handler()
