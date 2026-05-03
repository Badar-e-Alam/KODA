from analytics import total_revenue, top_customer

ORDERS = [
    {"customer": "Alice", "total": 50.00},
    {"customer": "Bob",   "total": 25.50},
    {"customer": "Alice", "total": 70.00},
    {"customer": "Carol", "total": 100.00},
    {"customer": "Bob",   "total": None},   # <-- this triggers the TypeError
]


def main():
    rev = total_revenue(ORDERS)
    print(f"Total revenue: ${rev:.2f}")
    name, total = top_customer(ORDERS)
    print(f"Top customer: {name} (${total:.2f})")


if __name__ == "__main__":
    main()
