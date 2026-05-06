def merge_records(records):
    """Merge list of dicts. Colliding keys should become lists."""
    result = {}
    for record in records:
        for k, v in record.items():
            result[k] = v
    return result
