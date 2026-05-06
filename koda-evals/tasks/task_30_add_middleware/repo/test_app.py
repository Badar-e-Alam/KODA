from app import App

def test_basic_route():
    app = App()
    app.register_route("/", lambda: "home")
    assert app.handle("/") == "home"

def test_middleware():
    app = App()
    app.register_route("/", lambda: "home")

    def add_prefix(handler):
        return lambda: "PREFIX:" + handler()

    app.use(add_prefix)
    assert app.handle("/") == "PREFIX:home"

def test_multiple_middleware():
    app = App()
    app.register_route("/", lambda: "home")

    def add_a(handler):
        return lambda: "A" + handler()

    def add_b(handler):
        return lambda: "B" + handler()

    app.use(add_a)
    app.use(add_b)
    assert app.handle("/") == "BAhome"

def test_middleware_after_register():
    app = App()
    app.register_route("/", lambda: "home")
    app.use(lambda h: lambda: "X" + h())
    app.register_route("/new", lambda: "new")
    assert app.handle("/new") == "Xnew"
