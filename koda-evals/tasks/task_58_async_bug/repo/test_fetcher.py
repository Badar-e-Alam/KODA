import asyncio
import time
from fetcher import fetch_all

def test_concurrent():
    urls = ["a", "b", "c"]
    start = time.time()
    result = asyncio.run(fetch_all(urls))
    elapsed = time.time() - start
    assert elapsed < 0.03  # 3 concurrent * 0.01s = ~0.01s, not 0.03s
    assert result == ["data:a", "data:b", "data:c"]
