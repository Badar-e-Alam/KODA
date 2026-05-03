"""Report generators with lots of duplication."""
import csv


def write_users_report(path, users):
    f = open(path, "w", newline="")
    try:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "email"])
        for u in users:
            writer.writerow([u["id"], u["name"], u["email"]])
    finally:
        f.close()


def write_orders_report(path, orders):
    f = open(path, "w", newline="")
    try:
        writer = csv.writer(f)
        writer.writerow(["order_id", "user_id", "total"])
        for o in orders:
            writer.writerow([o["order_id"], o["user_id"], o["total"]])
    finally:
        f.close()


def write_products_report(path, products):
    f = open(path, "w", newline="")
    try:
        writer = csv.writer(f)
        writer.writerow(["sku", "name", "price"])
        for p in products:
            writer.writerow([p["sku"], p["name"], p["price"]])
    finally:
        f.close()
