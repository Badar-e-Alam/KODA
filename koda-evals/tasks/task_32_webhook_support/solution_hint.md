# Solution
Add `register_webhook`, in `notify` check if webhook is set, use `urllib.request.Request` with json payload, try/except to fall back to console.
