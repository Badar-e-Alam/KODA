import pytest
from orders import process_order

def test_valid():
    raw = {
        "items": [{"name": "A", "qty": 2, "price": 10}],
        "customer": {"name": "Alice"},
    }
    assert process_order(raw)["total"] == 20

def test_not_dict():
    with pytest.raises(ValueError):
        process_order("bad")

def test_bad_item():
    with pytest.raises(ValueError):
        process_order({"items": [{"name": "A", "qty": 0}], "customer": {"name": "X"}})

def test_missing_customer_name():
    with pytest.raises(ValueError):
        process_order({"items": [{"name": "A", "qty": 1}], "customer": {}})
