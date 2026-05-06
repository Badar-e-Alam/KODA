import json
from datetime import datetime
from config import serialize_config

def test_basic():
    assert json.loads(serialize_config({"a": 1})) == {"a": 1}

def test_datetime():
    cfg = {"created": datetime(2024, 1, 15, 10, 30)}
    result = json.loads(serialize_config(cfg))
    assert result["created"] == "2024-01-15T10:30:00"

def test_set():
    cfg = {"tags": {"a", "b"}}
    result = json.loads(serialize_config(cfg))
    assert sorted(result["tags"]) == ["a", "b"]

def test_nested():
    cfg = {"items": [{"created": datetime(2024, 1, 1), "tags": {"x"}}]}
    result = json.loads(serialize_config(cfg))
    assert result["items"][0]["created"] == "2024-01-01T00:00:00"
    assert result["items"][0]["tags"] == ["x"]
