# Solution
Add `client=None` parameter, if None use `urllib.request.urlopen`, else call `client.open(url)`. In tests, pass a mock object with `.open()` returning a context manager with `.read()` returning JSON.
