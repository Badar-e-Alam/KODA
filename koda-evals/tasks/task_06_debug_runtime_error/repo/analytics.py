"""Analytics over a list of orders."""
from collections import defaultdict


def total_revenue(orders):
    """Sum the `total` field across all orders."""
    return sum(o["total"] for o in orders)


def top_customer(orders):
    """Return (name, total) of the customer with highest cumulative spend."""
    by_customer = defaultdict(float)
    for o in orders:
        by_customer[o["customer"]] += o["total"]
    # BUG: max with no key returns max of names, not totals
    name = max(by_customer)
    return name, by_customer[name]
