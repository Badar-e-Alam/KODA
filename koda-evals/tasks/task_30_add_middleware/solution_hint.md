# Solution
Store middlewares in a list. In `register_route`, wrap the handler with all middlewares (in reverse order so first added runs first). Or wrap at call time in `handle()`.
