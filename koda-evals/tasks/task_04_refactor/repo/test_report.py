import os
import tempfile
from report import write_users_report, write_orders_report, write_products_report


def _read(path):
    with open(path) as f:
        return f.read().strip().splitlines()


def test_users_report():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        path = f.name
    write_users_report(path, [
        {"id": 1, "name": "Alice", "email": "a@x.com"},
        {"id": 2, "name": "Bob", "email": "b@x.com"},
    ])
    lines = _read(path)
    os.unlink(path)
    assert lines[0] == "id,name,email"
    assert lines[1] == "1,Alice,a@x.com"
    assert lines[2] == "2,Bob,b@x.com"


def test_orders_report():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        path = f.name
    write_orders_report(path, [
        {"order_id": 100, "user_id": 1, "total": 25.50},
    ])
    lines = _read(path)
    os.unlink(path)
    assert lines[0] == "order_id,user_id,total"
    assert lines[1] == "100,1,25.5"


def test_products_report():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        path = f.name
    write_products_report(path, [
        {"sku": "A1", "name": "Widget", "price": 9.99},
    ])
    lines = _read(path)
    os.unlink(path)
    assert lines[0] == "sku,name,price"
    assert lines[1] == "A1,Widget,9.99"
