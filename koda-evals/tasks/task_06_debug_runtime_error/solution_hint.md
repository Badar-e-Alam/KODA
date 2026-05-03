# Solution

Two issues:

1. `total_revenue` crashes on `None` totals. Filter or default:
   ```python
   return sum(o["total"] for o in orders if o["total"] is not None)
   ```
   And same for `top_customer`'s aggregation.

2. `top_customer` uses `max(by_customer)` which max by *key* (name string),
   not by value. Use:
   ```python
   name = max(by_customer, key=by_customer.get)
   ```
