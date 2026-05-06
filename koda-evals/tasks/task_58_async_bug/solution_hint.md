# Solution
Use `asyncio.gather(*[_fetch(url) for url in urls])` instead of sequential await.
