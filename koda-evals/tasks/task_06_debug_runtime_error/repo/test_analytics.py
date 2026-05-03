from analytics import total_revenue, top_customer


def test_total_revenue_basic():
    orders = [{"customer": "A", "total": 10.0}, {"customer": "B", "total": 5.0}]
    assert total_revenue(orders) == 15.0


def test_total_revenue_skips_missing():
    orders = [
        {"customer": "A", "total": 10.0},
        {"customer": "B", "total": None},
        {"customer": "C", "total": 5.0},
    ]
    assert total_revenue(orders) == 15.0


def test_top_customer():
    orders = [
        {"customer": "A", "total": 10.0},
        {"customer": "B", "total": 50.0},
        {"customer": "A", "total": 5.0},
    ]
    name, total = top_customer(orders)
    assert name == "B"
    assert total == 50.0


def test_top_customer_aggregates():
    orders = [
        {"customer": "A", "total": 30.0},
        {"customer": "B", "total": 25.0},
        {"customer": "A", "total": 10.0},
    ]
    name, total = top_customer(orders)
    assert name == "A"
    assert total == 40.0
