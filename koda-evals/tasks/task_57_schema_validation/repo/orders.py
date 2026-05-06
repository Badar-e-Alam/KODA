def process_order(raw):
    """Process raw order dict. No validation currently."""
    total = sum(item["qty"] * item.get("price", 0) for item in raw["items"])
    return {
        "customer": raw["customer"]["name"],
        "total": total,
    }
