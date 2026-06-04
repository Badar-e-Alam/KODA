# Solution

```python
import csv


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def write_users_report(path, users):
    _write_csv(path, ["id", "name", "email"],
               ([u["id"], u["name"], u["email"]] for u in users))


def write_orders_report(path, orders):
    _write_csv(path, ["order_id", "user_id", "total"],
               ([o["order_id"], o["user_id"], o["total"]] for o in orders))


def write_products_report(path, products):
    _write_csv(path, ["sku", "name", "price"],
               ([p["sku"], p["name"], p["price"]] for p in products))
```
