import asyncio

async def _fetch(url):
    await asyncio.sleep(0.01)
    return f"data:{url}"

async def fetch_all(urls):
    """Fetch all URLs — currently sequential."""
    results = []
    for url in urls:
        results.append(await _fetch(url))  # sequential
    return results
