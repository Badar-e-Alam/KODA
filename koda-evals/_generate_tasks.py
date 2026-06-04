#!/usr/bin/env python3
"""Generate 50 new eval tasks for koda-evals.

Usage:
    cd koda-evals && python _generate_tasks.py

Creates tasks/task_15 through task_64.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

TASKS_DIR = Path(__file__).parent / "tasks"

# Curated subset of the 50 generator entries below. Set is intentionally
# narrow — see the picks audit for rationale. Add an entry back to this
# set to restore a dropped task; nothing else in this file needs to change.
KEEP_TASKS: set[int] = {
    17, 18, 20, 21, 23,                  # bugs
    26, 29, 30, 32,                       # features
    34, 35,                               # refactors
    39, 42,                               # perf
    47,                                   # tests
    49, 51,                               # frontend
    52, 54,                               # security
    57,                                   # data
    58,                                   # async
}


def write_task(num: int, name: str, prompt: str, test_sh: str, repo_files: dict[str, str], hint: str = "") -> None:
    if num not in KEEP_TASKS:
        return
    task_dir = TASKS_DIR / f"task_{num:02d}_{name}"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt.txt").write_text(prompt.strip() + "\n")
    (task_dir / "test.sh").write_text(test_sh.strip() + "\n")
    if hint:
        (task_dir / "solution_hint.md").write_text(hint.strip() + "\n")
    repo_dir = task_dir / "repo"
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir()
    for path, content in repo_files.items():
        full = repo_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)


# ============================================================
# TASK 15: Off-by-one in data processing
# ============================================================
write_task(
    15, "off_by_one",
    prompt="""The `get_weekly_slices(data, week_size)` function in `slicer.py` is supposed to return exactly `len(data) // week_size` chunks, but it's returning one extra empty chunk at the end when `len(data)` is perfectly divisible by `week_size`.

Fix the bug so all tests in `test_slicer.py` pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_slicer.py -q""",
    repo_files={
        "slicer.py": """def get_weekly_slices(data, week_size=7):
    \"\"\"Split data into chunks of week_size.\"\"\"
    slices = []
    for i in range(0, len(data), week_size):
        slices.append(data[i : i + week_size])
    # Bug: includes empty slice when len(data) % week_size == 0
    slices.append(data[len(data) : len(data) + week_size])
    return slices
""",
        "test_slicer.py": """import pytest
from slicer import get_weekly_slices

def test_exact_divisible():
    assert get_weekly_slices([1, 2, 3, 4, 5, 6, 7], 7) == [[1, 2, 3, 4, 5, 6, 7]]

def test_not_divisible():
    assert get_weekly_slices([1, 2, 3, 4, 5], 3) == [[1, 2, 3], [4, 5]]

def test_empty():
    assert get_weekly_slices([], 7) == []

def test_small_data():
    assert get_weekly_slices([1], 7) == [[1]]
""",
    },
    hint="""# Solution
Remove the extra `slices.append(data[len(data) : len(data) + week_size])` line that always appends an empty slice.""",
)


# ============================================================
# TASK 16: String encoding bug
# ============================================================
write_task(
    16, "encoding_bug",
    prompt="""The `normalize_name(name)` function in `names.py` is supposed to strip whitespace, convert to lowercase, and replace non-ASCII characters with their ASCII equivalents (e.g., 'é' → 'e'). However, it fails on names with accents because it processes bytes instead of unicode characters.

Fix the bug so all tests in `test_names.py` pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_names.py -q""",
    repo_files={
        "names.py": """import unicodedata

def normalize_name(name):
    \"\"\"Normalize a name: lowercase, strip, ascii-fold.\"\"\"
    name = name.lower().strip()
    # Bug: iterating over bytes instead of characters
    return "".join(
        chr(c) if c < 128 else unicodedata.lookup("LATIN SMALL LETTER " + chr(c))
        for c in name.encode("utf-8")
    )
""",
        "test_names.py": """from names import normalize_name

def test_basic():
    assert normalize_name("  Alice  ") == "alice"

def test_accent():
    assert normalize_name("José") == "jose"

def test_german():
    assert normalize_name("Müller") == "muller"

def test_french():
    assert normalize_name("François") == "francois"
""",
    },
    hint="""# Solution
Iterate over characters (not bytes) and use `unicodedata.normalize('NFKD', char)` to decompose accented characters, then filter to ASCII.""",
)


# ============================================================
# TASK 17: Date timezone bug
# ============================================================
write_task(
    17, "timezone_bug",
    prompt="""The `is_due_today(due_date)` function in `scheduler.py` compares a due date (naive datetime) against `datetime.now()`. The comparison fails when the due date is from a different timezone because it doesn't account for timezone offsets.

Fix the bug so all tests in `test_scheduler.py` pass. Treat naive datetimes as UTC.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_scheduler.py -q""",
    repo_files={
        "scheduler.py": """from datetime import datetime, timezone

def is_due_today(due_date):
    \"\"\"Return True if due_date is today (UTC).\"\"\"
    now = datetime.now()
    return due_date.date() == now.date()
""",
        "test_scheduler.py": """from datetime import datetime, timezone, timedelta
from scheduler import is_due_today

def test_same_day():
    now = datetime.now(timezone.utc)
    assert is_due_today(now) is True

def test_different_timezone():
    # Due date is 23:00 UTC yesterday, but 01:00 today in +02:00
    due = datetime(2024, 1, 15, 23, 0, tzinfo=timezone(timedelta(hours=2)))
    # Mock: pretend "now" is 2024-01-16 00:30 UTC
    # The function should compare in UTC, not local time
    assert is_due_today(due.astimezone(timezone.utc).replace(tzinfo=None)) is True

def test_past():
    past = datetime(2020, 1, 1)
    assert is_due_today(past) is False
""",
    },
    hint="""# Solution
Compare both dates in UTC: convert naive datetimes with `.replace(tzinfo=timezone.utc)` and aware datetimes with `.astimezone(timezone.utc)`.""",
)


# ============================================================
# TASK 18: JSON serialization edge case
# ============================================================
write_task(
    18, "json_serialize",
    prompt="""The `serialize_config(config)` function in `config.py` uses `json.dumps()` but fails when the config contains `datetime` objects or `set` instances because they aren't JSON serializable.

Fix it to handle both types: `datetime` → ISO format string, `set` → list. All tests in `test_config.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_config.py -q""",
    repo_files={
        "config.py": """import json
from datetime import datetime

def serialize_config(config):
    \"\"\"Serialize config dict to JSON string.\"\"\"
    return json.dumps(config)
""",
        "test_config.py": """import json
from datetime import datetime
from config import serialize_config

def test_basic():
    assert json.loads(serialize_config({"a": 1})) == {"a": 1}

def test_datetime():
    cfg = {"created": datetime(2024, 1, 15, 10, 30)}
    result = json.loads(serialize_config(cfg))
    assert result["created"] == "2024-01-15T10:30:00"

def test_set():
    cfg = {"tags": {"a", "b"}}
    result = json.loads(serialize_config(cfg))
    assert sorted(result["tags"]) == ["a", "b"]

def test_nested():
    cfg = {"items": [{"created": datetime(2024, 1, 1), "tags": {"x"}}]}
    result = json.loads(serialize_config(cfg))
    assert result["items"][0]["created"] == "2024-01-01T00:00:00"
    assert result["items"][0]["tags"] == ["x"]
""",
    },
    hint="""# Solution
Provide a custom `default` callback to `json.dumps()` that checks `isinstance(obj, datetime)` and `isinstance(obj, set)`.""",
)


# ============================================================
# TASK 19: Floating point comparison
# ============================================================
write_task(
    19, "float_compare",
    prompt="""The `are_balances_equal(a, b)` function in `ledger.py` uses `==` to compare monetary balances, but fails on floating-point rounding errors.

Rewrite it to compare within a tolerance of `0.001`. All tests in `test_ledger.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_ledger.py -q""",
    repo_files={
        "ledger.py": """def are_balances_equal(a, b):
    \"\"\"Compare two monetary balances.\"\"\"
    return a == b
""",
        "test_ledger.py": """from ledger import are_balances_equal

def test_exact():
    assert are_balances_equal(100.0, 100.0) is True

def test_fp_rounding():
    assert are_balances_equal(0.1 + 0.2, 0.3) is True

def test_different():
    assert are_balances_equal(100.0, 100.01) is False

def test_within_tolerance():
    assert are_balances_equal(100.0, 100.0005) is True

def test_outside_tolerance():
    assert are_balances_equal(100.0, 100.002) is False
""",
    },
    hint="""# Solution
Use `abs(a - b) <= 0.001` instead of `==`.""",
)


# ============================================================
# TASK 20: List mutation during iteration
# ============================================================
write_task(
    20, "mutation_iteration",
    prompt="""The `remove_expired(items)` function in `inventory.py` removes expired items from a list while iterating over it, causing some items to be skipped.

Fix the bug so all expired items are removed. All tests in `test_inventory.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_inventory.py -q""",
    repo_files={
        "inventory.py": """from datetime import datetime

def remove_expired(items):
    \"\"\"Remove expired items from list. Each item is a dict with 'expiry' datetime.\"\"\"
    now = datetime.now()
    for item in items:
        if item["expiry"] < now:
            items.remove(item)
    return items
""",
        "test_inventory.py": """from datetime import datetime, timedelta
from inventory import remove_expired

def test_no_expired():
    items = [{"name": "a", "expiry": datetime(2099, 1, 1)}]
    assert remove_expired(items) == items

def test_all_expired():
    items = [
        {"name": "a", "expiry": datetime(2020, 1, 1)},
        {"name": "b", "expiry": datetime(2020, 1, 2)},
    ]
    assert remove_expired(items) == []

def test_mixed():
    now = datetime.now()
    items = [
        {"name": "a", "expiry": now - timedelta(days=1)},
        {"name": "b", "expiry": now + timedelta(days=1)},
        {"name": "c", "expiry": now - timedelta(days=2)},
    ]
    result = remove_expired(items)
    assert [i["name"] for i in result] == ["b"]

def test_consecutive_expired():
    now = datetime.now()
    items = [
        {"name": "a", "expiry": now - timedelta(days=1)},
        {"name": "b", "expiry": now - timedelta(days=2)},
        {"name": "c", "expiry": now + timedelta(days=1)},
    ]
    result = remove_expired(items)
    assert [i["name"] for i in result] == ["c"]
""",
    },
    hint="""# Solution
Iterate over a copy of the list, or build a new list with list comprehension, or iterate in reverse.""",
)


# ============================================================
# TASK 21: Dictionary key collision
# ============================================================
write_task(
    21, "dict_collision",
    prompt="""The `merge_records(records)` function in `merger.py` merges a list of dicts into one, but overwrites values when keys collide instead of collecting them into lists.

Fix it so colliding keys produce lists of all values. All tests in `test_merger.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_merger.py -q""",
    repo_files={
        "merger.py": """def merge_records(records):
    \"\"\"Merge list of dicts. Colliding keys should become lists.\"\"\"
    result = {}
    for record in records:
        for k, v in record.items():
            result[k] = v
    return result
""",
        "test_merger.py": """from merger import merge_records

def test_no_collision():
    assert merge_records([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}

def test_collision():
    assert merge_records([{"a": 1}, {"a": 2}]) == {"a": [1, 2]}

def test_mixed():
    assert merge_records([{"a": 1}, {"a": 2}, {"b": 3}, {"a": 4}]) == {"a": [1, 2, 4], "b": 3}

def test_single():
    assert merge_records([{"a": 1}]) == {"a": 1}

def test_empty():
    assert merge_records([]) == {}
""",
    },
    hint="""# Solution
Check if key already exists: if it does and isn't a list, convert to list and append; if it's already a list, append.""",
)


# ============================================================
# TASK 22: Regex pattern bug
# ============================================================
write_task(
    22, "regex_bug",
    prompt="""The `extract_emails(text)` function in `extractor.py` uses a regex that misses email addresses with plus signs (e.g., `user+tag@example.com`) and dots before the @.

Fix the regex so all valid email formats in `test_extractor.py` are captured.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_extractor.py -q""",
    repo_files={
        "extractor.py": """import re

def extract_emails(text):
    \"\"\"Extract email addresses from text.\"\"\"
    pattern = r"[a-zA-Z0-9]+@[a-zA-Z0-9]+\\.[a-zA-Z]+"
    return re.findall(pattern, text)
""",
        "test_extractor.py": """from extractor import extract_emails

def test_simple():
    assert extract_emails("Contact alice@example.com") == ["alice@example.com"]

def test_plus_sign():
    assert extract_emails("Use alice+tag@example.com") == ["alice+tag@example.com"]

def test_dot_local():
    assert extract_emails("Reach alice.smith@example.com") == ["alice.smith@example.com"]

def test_multiple():
    text = "a@x.com and b.c+d@y.co.uk"
    assert sorted(extract_emails(text)) == sorted(["a@x.com", "b.c+d@y.co.uk"])

def test_no_match():
    assert extract_emails("no emails here") == []
""",
    },
    hint="""# Solution
Use a more permissive local-part pattern: `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}`""",
)


# ============================================================
# TASK 23: Circular import
# ============================================================
write_task(
    23, "circular_import",
    prompt="""The `models.py` and `validators.py` modules have a circular import that crashes on import. `models.py` imports `validators` at the top, and `validators.py` imports `models` inside a function.

Restructure the code to eliminate the circular import while keeping all functionality. All tests in `test_models.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_models.py -q""",
    repo_files={
        "models.py": """import validators  # circular import

class User:
    def __init__(self, email):
        self.email = email

    def is_valid(self):
        return validators.is_email(self.email)
""",
        "validators.py": """# This causes circular import when models is loaded
from models import User

def is_email(value):
    return "@" in value

def validate_user(user):
    return isinstance(user, User) and is_email(user.email)
""",
        "test_models.py": """from models import User
from validators import is_email, validate_user

def test_user_valid():
    u = User("alice@example.com")
    assert u.is_valid() is True

def test_user_invalid():
    u = User("not-an-email")
    assert u.is_valid() is False

def test_validate_user():
    u = User("alice@example.com")
    assert validate_user(u) is True
""",
    },
    hint="""# Solution
Move the `from models import User` in validators.py to inside the `validate_user` function (lazy import), or remove it entirely and use duck typing (`hasattr(user, 'email')`).""",
)


# ============================================================
# TASK 24: Race condition simulation
# ============================================================
write_task(
    24, "race_condition",
    prompt="""The `Counter` class in `counter.py` uses a simple increment method that is not thread-safe. When called from multiple threads, counts are lost.

Make it thread-safe using `threading.Lock`. All tests in `test_counter.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_counter.py -q""",
    repo_files={
        "counter.py": """class Counter:
    def __init__(self):
        self.value = 0

    def increment(self):
        # Not thread-safe
        current = self.value
        self.value = current + 1

    def get(self):
        return self.value
""",
        "test_counter.py": """import threading
from counter import Counter

def test_thread_safety():
    c = Counter()
    threads = []
    for _ in range(10):
        t = threading.Thread(target=lambda: [c.increment() for _ in range(100)])
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    assert c.get() == 1000
""",
    },
    hint="""# Solution
Add `self._lock = threading.Lock()` in `__init__` and wrap `increment` body with `with self._lock:`.""",
)


# ============================================================
# TASK 25: Add validation to class
# ============================================================
write_task(
    25, "add_validation",
    prompt="""The `Product` class in `product.py` accepts any price and quantity without validation. Add input validation:

- `price` must be a positive number (int or float)
- `quantity` must be a non-negative integer
- Raise `ValueError` with clear messages for invalid inputs

All tests in `test_product.py` must pass. Existing behavior for valid inputs must not change.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_product.py -q""",
    repo_files={
        "product.py": """class Product:
    def __init__(self, name, price, quantity):
        self.name = name
        self.price = price
        self.quantity = quantity

    def total_value(self):
        return self.price * self.quantity
""",
        "test_product.py": """import pytest
from product import Product

def test_valid():
    p = Product("Widget", 10.5, 3)
    assert p.total_value() == 31.5

def test_invalid_price_negative():
    with pytest.raises(ValueError, match="price"):
        Product("X", -1, 1)

def test_invalid_price_zero():
    with pytest.raises(ValueError, match="price"):
        Product("X", 0, 1)

def test_invalid_quantity_negative():
    with pytest.raises(ValueError, match="quantity"):
        Product("X", 1, -1)

def test_invalid_quantity_float():
    with pytest.raises(ValueError, match="quantity"):
        Product("X", 1, 1.5)

def test_total_value_zero_quantity():
    p = Product("X", 10, 0)
    assert p.total_value() == 0
""",
    },
    hint="""# Solution
Add validation in `__init__`: check `isinstance(price, (int, float)) and price > 0`, and `isinstance(quantity, int) and quantity >= 0`.""",
)


# ============================================================
# TASK 26: Add pagination
# ============================================================
write_task(
    26, "add_pagination",
    prompt="""The `list_users()` function in `users.py` returns all users at once. Add pagination support:

    list_users(page=1, per_page=10)

It should:
- Return a dict with `{"users": [...], "total": N, "page": page, "per_page": per_page}`
- Validate that `page` and `per_page` are positive integers
- Return empty `users` list and correct `total` for out-of-range pages

All tests in `test_users.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_users.py -q""",
    repo_files={
        "users.py": """_USERS = [{"id": i, "name": f"User {i}"} for i in range(1, 26)]

def list_users():
    \"\"\"Return all users.\"\"\"
    return _USERS
""",
        "test_users.py": """import pytest
from users import list_users

def test_default_pagination():
    result = list_users()
    assert result["users"] == _USERS[:10]
    assert result["total"] == 25

def test_second_page():
    result = list_users(page=2, per_page=10)
    assert len(result["users"]) == 10
    assert result["page"] == 2

def test_last_partial_page():
    result = list_users(page=3, per_page=10)
    assert len(result["users"]) == 5

def test_out_of_range():
    result = list_users(page=10, per_page=10)
    assert result["users"] == []
    assert result["total"] == 25

def test_invalid_page():
    with pytest.raises(ValueError):
        list_users(page=0)

def test_invalid_per_page():
    with pytest.raises(ValueError):
        list_users(per_page=-1)
""",
    },
    hint="""# Solution
Change signature to `list_users(page=1, per_page=10)`, validate inputs, slice `_USERS[(page-1)*per_page : page*per_page]`, return the dict.""",
)


# ============================================================
# TASK 27: Add filtering and sorting
# ============================================================
write_task(
    27, "filter_sort",
    prompt="""The `search_products(products, query)` function in `catalog.py` only does substring matching. Extend it to support:

- `sort_by`: "name" or "price" (default no sorting)
- `order`: "asc" or "desc" (default "asc")
- `min_price` and `max_price` filters

Return the filtered, sorted list. All tests in `test_catalog.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_catalog.py -q""",
    repo_files={
        "catalog.py": """def search_products(products, query):
    \"\"\"Filter products by name substring.\"\"\"
    return [p for p in products if query.lower() in p["name"].lower()]
""",
        "test_catalog.py": """from catalog import search_products

PRODUCTS = [
    {"name": "Apple", "price": 1.0},
    {"name": "Banana", "price": 0.5},
    {"name": "Cherry", "price": 3.0},
    {"name": "Apricot", "price": 2.0},
]

def test_basic_search():
    assert len(search_products(PRODUCTS, "a")) == 3

def test_sort_by_name_asc():
    result = search_products(PRODUCTS, "a", sort_by="name")
    assert [p["name"] for p in result] == ["Apple", "Apricot", "Banana"]

def test_sort_by_price_desc():
    result = search_products(PRODUCTS, "a", sort_by="price", order="desc")
    assert [p["price"] for p in result] == [2.0, 1.0, 0.5]

def test_min_price_filter():
    result = search_products(PRODUCTS, "", min_price=1.5)
    assert len(result) == 2

def test_max_price_filter():
    result = search_products(PRODUCTS, "", max_price=1.0)
    assert len(result) == 2

def test_combined():
    result = search_products(PRODUCTS, "a", sort_by="price", order="desc", min_price=1.0)
    assert [p["price"] for p in result] == [2.0, 1.0]
""",
    },
    hint="""# Solution
Add optional parameters, apply price filters first, then substring search, then sort with `sorted()` and reverse= based on order.""",
)


# ============================================================
# TASK 28: Add export format
# ============================================================
write_task(
    28, "add_export",
    prompt="""The `Exporter` class in `exporter.py` only supports CSV. Add support for JSON export:

    exporter.export(data, format="json")

- `format` defaults to "csv"
- Raise `ValueError` for unsupported formats
- JSON should be a pretty-printed string with 2-space indentation

All tests in `test_exporter.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_exporter.py -q""",
    repo_files={
        "exporter.py": """import csv
import io

class Exporter:
    def export(self, data, format="csv"):
        \"\"\"Export list of dicts to a string.\"\"\"
        if not data:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()
""",
        "test_exporter.py": """import json
import pytest
from exporter import Exporter

def test_csv():
    e = Exporter()
    result = e.export([{"a": 1, "b": 2}])
    assert "a,b" in result
    assert "1,2" in result

def test_json():
    e = Exporter()
    result = e.export([{"a": 1, "b": 2}], format="json")
    parsed = json.loads(result)
    assert parsed == [{"a": 1, "b": 2}]

def test_json_indent():
    e = Exporter()
    result = e.export([{"a": 1}], format="json")
    assert "  " in result  # indented

def test_invalid_format():
    with pytest.raises(ValueError):
        Exporter().export([], format="xml")

def test_empty_json():
    result = Exporter().export([], format="json")
    assert json.loads(result) == []
""",
    },
    hint="""# Solution
Check `format` parameter, if "json" use `json.dumps(data, indent=2)`, else keep existing CSV logic, raise ValueError otherwise.""",
)


# ============================================================
# TASK 29: Add caching layer
# ============================================================
write_task(
    29, "add_caching",
    prompt="""The `fetch_user(user_id)` function in `users_api.py` always calls the expensive `_slow_lookup`. Add an in-memory LRU cache so repeated lookups for the same `user_id` are instant.

Requirements:
- Cache up to 100 entries
- Return cached results for repeated calls
- Thread-safe (use `threading.Lock`)
- The cache should be stored at module level

All tests in `test_users_api.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_users_api.py -q""",
    repo_files={
        "users_api.py": """import time

def _slow_lookup(user_id):
    time.sleep(0.01)  # simulate network
    return {"id": user_id, "name": f"User {user_id}"}

def fetch_user(user_id):
    \"\"\"Fetch user — currently always slow.\"\"\"
    return _slow_lookup(user_id)
""",
        "test_users_api.py": """import time
from users_api import fetch_user

def test_returns_user():
    u = fetch_user(1)
    assert u["id"] == 1

def test_caching():
    start = time.time()
    fetch_user(42)
    fetch_user(42)
    fetch_user(42)
    elapsed = time.time() - start
    assert elapsed < 0.03  # should be ~0.01s, not 0.03s

def test_different_users():
    assert fetch_user(1)["name"] == "User 1"
    assert fetch_user(2)["name"] == "User 2"
""",
    },
    hint="""# Solution
Add a module-level dict cache and Lock. Check cache before calling `_slow_lookup`, store result, evict oldest if >100 entries (or use `collections.OrderedDict` / simple dict with tracking).""",
)


# ============================================================
# TASK 30: Add middleware/interceptor
# ============================================================
write_task(
    30, "add_middleware",
    prompt="""The `App` class in `app.py` has a `register_route` method. Add a `use(middleware)` method that wraps all registered routes so middleware runs before the route handler.

A middleware is a callable `(handler) -> new_handler`.

All tests in `test_app.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_app.py -q""",
    repo_files={
        "app.py": """class App:
    def __init__(self):
        self.routes = {}

    def register_route(self, path, handler):
        self.routes[path] = handler

    def handle(self, path):
        handler = self.routes.get(path)
        if handler is None:
            return "404"
        return handler()
""",
        "test_app.py": """from app import App

def test_basic_route():
    app = App()
    app.register_route("/", lambda: "home")
    assert app.handle("/") == "home"

def test_middleware():
    app = App()
    app.register_route("/", lambda: "home")

    def add_prefix(handler):
        return lambda: "PREFIX:" + handler()

    app.use(add_prefix)
    assert app.handle("/") == "PREFIX:home"

def test_multiple_middleware():
    app = App()
    app.register_route("/", lambda: "home")

    def add_a(handler):
        return lambda: "A" + handler()

    def add_b(handler):
        return lambda: "B" + handler()

    app.use(add_a)
    app.use(add_b)
    assert app.handle("/") == "BAhome"

def test_middleware_after_register():
    app = App()
    app.register_route("/", lambda: "home")
    app.use(lambda h: lambda: "X" + h())
    app.register_route("/new", lambda: "new")
    assert app.handle("/new") == "Xnew"
""",
    },
    hint="""# Solution
Store middlewares in a list. In `register_route`, wrap the handler with all middlewares (in reverse order so first added runs first). Or wrap at call time in `handle()`.""",
)


# ============================================================
# TASK 31: Add CLI subcommand
# ============================================================
write_task(
    31, "cli_subcommand",
    prompt="""The `cli.py` script only has an `add` command. Add a `remove` subcommand that:

- Takes `--id` (required integer)
- Prints `Removed item <id>` if id > 0
- Prints `Invalid id` and exits with code 1 if id <= 0
- Use `argparse` subparsers

All tests in `test_cli.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_cli.py -q""",
    repo_files={
        "cli.py": """import argparse

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    add_parser = sub.add_parser("add")
    add_parser.add_argument("--name", required=True)

    args = parser.parse_args()
    if args.command == "add":
        print(f"Added {args.name}")

if __name__ == "__main__":
    main()
""",
        "test_cli.py": """import subprocess
import sys

def test_add():
    result = subprocess.run([sys.executable, "cli.py", "add", "--name", "X"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Added X" in result.stdout

def test_remove():
    result = subprocess.run([sys.executable, "cli.py", "remove", "--id", "5"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Removed item 5" in result.stdout

def test_remove_invalid():
    result = subprocess.run([sys.executable, "cli.py", "remove", "--id", "0"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Invalid id" in result.stdout
""",
    },
    hint="""# Solution
Add `remove_parser = sub.add_parser("remove")`, add `--id` argument, handle the remove command in main.""",
)


# ============================================================
# TASK 32: Add webhook support
# ============================================================
write_task(
    32, "webhook_support",
    prompt="""The `Notifier` class in `notifier.py` only logs to console. Add webhook support:

- `register_webhook(url)` — stores the URL
- `notify(message)` — POSTs JSON `{"text": message}` to the webhook if registered
- If webhook call fails (simulate with a bool flag), fall back to console
- Use `urllib.request` (no external deps)

All tests in `test_notifier.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_notifier.py -q""",
    repo_files={
        "notifier.py": """class Notifier:
    def __init__(self):
        self.webhook_url = None

    def notify(self, message):
        print(f"[console] {message}")
""",
        "test_notifier.py": """import json
from unittest.mock import patch, MagicMock
from notifier import Notifier

def test_console_only():
    n = Notifier()
    # Just shouldn't crash
    n.notify("hello")

def test_webhook_success():
    n = Notifier()
    n.register_webhook("http://example.com/hook")

    with patch("notifier.urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response
        n.notify("hello")
        # Verify it was called
        assert mock_urlopen.called
""",
    },
    hint="""# Solution
Add `register_webhook`, in `notify` check if webhook is set, use `urllib.request.Request` with json payload, try/except to fall back to console.""",
)


# ============================================================
# TASK 33: Extract repeated logic
# ============================================================
write_task(
    33, "extract_helper",
    prompt="""The `reports.py` module has three functions that all validate that a file path ends with `.csv` and raise `ValueError` if not. Extract this validation into a shared helper `_validate_csv_path(path)` and use it in all three functions.

All tests in `test_reports.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_reports.py -q""",
    repo_files={
        "reports.py": """def export_sales(path, data):
    if not path.endswith(".csv"):
        raise ValueError("Path must end with .csv")
    with open(path, "w") as f:
        f.write("sales,data\\n")
        for row in data:
            f.write(f"{row}\\n")

def export_inventory(path, data):
    if not path.endswith(".csv"):
        raise ValueError("Path must end with .csv")
    with open(path, "w") as f:
        f.write("inventory,data\\n")
        for row in data:
            f.write(f"{row}\\n")

def export_users(path, data):
    if not path.endswith(".csv"):
        raise ValueError("Path must end with .csv")
    with open(path, "w") as f:
        f.write("users,data\\n")
        for row in data:
            f.write(f"{row}\\n")
""",
        "test_reports.py": """import pytest
from reports import export_sales, export_inventory, export_users

def test_sales_valid():
    export_sales("/tmp/sales.csv", ["a"])

def test_sales_invalid():
    with pytest.raises(ValueError, match=".csv"):
        export_sales("/tmp/sales.txt", ["a"])

def test_inventory_invalid():
    with pytest.raises(ValueError, match=".csv"):
        export_inventory("/tmp/inv.txt", ["a"])

def test_users_invalid():
    with pytest.raises(ValueError, match=".csv"):
        export_users("/tmp/users.txt", ["a"])
""",
    },
    hint="""# Solution
Define `_validate_csv_path(path)` and call it at the top of each export function.""",
)


# ============================================================
# TASK 34: Replace inheritance with composition
# ============================================================
write_task(
    34, "composition",
    prompt="""The `PremiumCustomer` class in `customers.py` inherits from `Customer` but only uses it for the `discount` method. Refactor to use composition instead: `PremiumCustomer` should accept a `Customer` instance and delegate discount calculations to it.

The public API must remain unchanged. All tests in `test_customers.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_customers.py -q""",
    repo_files={
        "customers.py": """class Customer:
    def __init__(self, name, base_discount=0.0):
        self.name = name
        self.base_discount = base_discount

    def discount(self):
        return self.base_discount

class PremiumCustomer(Customer):
    def __init__(self, name, base_discount=0.0, bonus=0.1):
        super().__init__(name, base_discount)
        self.bonus = bonus

    def discount(self):
        return super().discount() + self.bonus
""",
        "test_customers.py": """from customers import Customer, PremiumCustomer

def test_customer():
    c = Customer("Alice", 0.05)
    assert c.discount() == 0.05

def test_premium():
    p = PremiumCustomer("Bob", 0.05, 0.1)
    assert p.discount() == 0.15
    assert p.name == "Bob"

def test_premium_zero_base():
    p = PremiumCustomer("Carol", 0.0, 0.2)
    assert p.discount() == 0.2
""",
    },
    hint="""# Solution
Make `PremiumCustomer.__init__` accept a `customer` parameter, store it, and delegate `self.customer.discount()` + bonus.""",
)


# ============================================================
# TASK 35: Remove global state
# ============================================================
write_task(
    35, "remove_globals",
    prompt="""The `logger.py` module uses a module-level global `LOG_LEVEL` that can't be configured per-instance. Refactor to a `Logger` class where each instance has its own `level`.

All tests in `test_logger.py` must pass. Keep backward compatibility by providing module-level functions that use a default instance.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_logger.py -q""",
    repo_files={
        "logger.py": """LOG_LEVEL = "INFO"

def debug(msg):
    if LOG_LEVEL == "DEBUG":
        print(f"[DEBUG] {msg}")

def info(msg):
    if LOG_LEVEL in ("DEBUG", "INFO"):
        print(f"[INFO] {msg}")
""",
        "test_logger.py": """import logger

def test_default_info():
    logger.info("hello")  # should print

def test_per_instance_level():
    l = logger.Logger("DEBUG")
    # debug should print for this instance
    l.debug("test")

def test_quiet_instance():
    l = logger.Logger("ERROR")
    # info should not print
    l.info("silent")
""",
    },
    hint="""# Solution
Create `Logger` class with `__init__(self, level="INFO")`. Add module-level `_default_logger = Logger()` and make module-level `debug`/`info` delegate to it.""",
)


# ============================================================
# TASK 36: Consolidate error handling
# ============================================================
write_task(
    36, "consolidate_errors",
    prompt="""The `api.py` module has three route handlers that each handle 404 errors differently. Extract a consistent `not_found(resource)` helper and use it everywhere.

All tests in `test_api.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_api.py -q""",
    repo_files={
        "api.py": """def get_user(user_id):
    users = {1: "Alice", 2: "Bob"}
    if user_id not in users:
        return {"error": "User not found", "status": 404}
    return {"data": users[user_id]}

def get_product(product_id):
    products = {1: "Widget"}
    if product_id not in products:
        return {"error": "Product not found", "status": 404}
    return {"data": products[product_id]}

def get_order(order_id):
    orders = {10: "Order#10"}
    if order_id not in orders:
        return {"error": "Order not found", "status": 404}
    return {"data": orders[order_id]}
""",
        "test_api.py": """from api import get_user, get_product, get_order

def test_user_found():
    assert get_user(1)["data"] == "Alice"

def test_user_not_found():
    r = get_user(999)
    assert r["status"] == 404
    assert "User" in r["error"]

def test_product_not_found():
    r = get_product(999)
    assert r["status"] == 404
    assert "Product" in r["error"]

def test_order_not_found():
    r = get_order(999)
    assert r["status"] == 404
    assert "Order" in r["error"]
""",
    },
    hint="""# Solution
Define `not_found(resource)` returning `{"error": f"{resource} not found", "status": 404}` and use it in all three handlers.""",
)


# ============================================================
# TASK 37: Extract configuration
# ============================================================
write_task(
    37, "extract_config",
    prompt="""The `database.py` module hardcodes connection parameters. Extract them into a `Config` dataclass and modify `connect()` to accept it.

All tests in `test_database.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_database.py -q""",
    repo_files={
        "database.py": """def connect():
    \"\"\"Connect to database with hardcoded params.\"\"\"
    host = "localhost"
    port = 5432
    user = "admin"
    password = "secret"
    return f"postgresql://{user}:{password}@{host}:{port}/db"
""",
        "test_database.py": """from database import connect, Config

def test_connect_with_config():
    cfg = Config(host="db.example.com", port=5433, user="app", password="pass")
    assert connect(cfg) == "postgresql://app:pass@db.example.com:5433/db"

def test_connect_defaults():
    cfg = Config()
    assert connect(cfg) == "postgresql://admin:secret@localhost:5432/db"
""",
    },
    hint="""# Solution
Define `@dataclass class Config: host="localhost", port=5432, user="admin", password="secret"` and use it in `connect(cfg)`.""",
)


# ============================================================
# TASK 38: Simplify nested conditionals
# ============================================================
write_task(
    38, "simplify_conditions",
    prompt="""The `can_access(user, resource)` function in `auth.py` has deeply nested conditionals. Refactor it to use early returns and flatten the structure while preserving exact behavior.

All tests in `test_auth.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_auth.py -q""",
    repo_files={
        "auth.py": """def can_access(user, resource):
    if user is not None:
        if user.get("active"):
            if resource.get("public"):
                return True
            else:
                if user.get("role") == "admin":
                    return True
                else:
                    if user.get("role") == "editor" and resource.get("owner") == user.get("id"):
                        return True
                    else:
                        return False
        else:
            return False
    else:
        return False
""",
        "test_auth.py": """from auth import can_access

def test_admin_private():
    assert can_access({"active": True, "role": "admin"}, {"public": False}) is True

def test_editor_own():
    assert can_access({"active": True, "role": "editor", "id": 1}, {"public": False, "owner": 1}) is True

def test_editor_other():
    assert can_access({"active": True, "role": "editor", "id": 1}, {"public": False, "owner": 2}) is False

def test_public():
    assert can_access({"active": True, "role": "user"}, {"public": True}) is True

def test_inactive():
    assert can_access({"active": False, "role": "admin"}, {"public": True}) is False

def test_no_user():
    assert can_access(None, {"public": True}) is False
""",
    },
    hint="""# Solution
Use early returns: `if not user: return False`, `if not user.get("active"): return False`, `if resource.get("public"): return True`, etc.""",
)


# ============================================================
# TASK 39: Memoization
# ============================================================
write_task(
    39, "memoization",
    prompt="""The `fib(n)` function in `fib.py` recalculates the same values repeatedly. Add memoization so it runs in O(n) time instead of exponential.

All tests in `test_fib.py` must pass, including a stress test for `fib(100)`.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_fib.py -q""",
    repo_files={
        "fib.py": """def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)
""",
        "test_fib.py": """from fib import fib

def test_base():
    assert fib(0) == 0
    assert fib(1) == 1

def test_small():
    assert fib(10) == 55

def test_large():
    assert fib(100) == 354224848179261915075
""",
    },
    hint="""# Solution
Add `cache = {}` or `functools.lru_cache`, store computed values, check cache before recursing.""",
)


# ============================================================
# TASK 40: Lazy loading
# ============================================================
write_task(
    40, "lazy_loading",
    prompt="""The `Settings` class in `settings.py` loads all config files in `__init__`, which is slow. Refactor to lazy-load each config file only when its property is first accessed.

All tests in `test_settings.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_settings.py -q""",
    repo_files={
        "settings.py": """import time

def _slow_load(path):
    time.sleep(0.05)
    return {"data": path}

class Settings:
    def __init__(self):
        self.db = _slow_load("db.conf")
        self.cache = _slow_load("cache.conf")
        self.api = _slow_load("api.conf")
""",
        "test_settings.py": """import time
from settings import Settings

def test_lazy():
    s = Settings()
    start = time.time()
    # Only access one property
    _ = s.db
    elapsed = time.time() - start
    assert elapsed < 0.1  # should be ~0.05, not 0.15

def test_all_loaded():
    s = Settings()
    assert s.db["data"] == "db.conf"
    assert s.cache["data"] == "cache.conf"
    assert s.api["data"] == "api.conf"
""",
    },
    hint="""# Solution
Use `@property` decorators that call `_slow_load` on first access and cache the result in an instance dict.""",
)


# ============================================================
# TASK 41: Batch processing
# ============================================================
write_task(
    41, "batch_processing",
    prompt="""The `process_items(items)` function in `processor.py` processes items one at a time, making a slow call per item. Rewrite it to process in batches of 10, calling `_slow_batch_call(batch)` once per batch.

All tests in `test_processor.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_processor.py -q""",
    repo_files={
        "processor.py": """import time

def _slow_batch_call(batch):
    time.sleep(0.01)
    return [f"processed:{x}" for x in batch]

def process_items(items):
    \"\"\"Process items one by one — too slow.\"\"\"
    results = []
    for item in items:
        results.extend(_slow_batch_call([item]))
    return results
""",
        "test_processor.py": """import time
from processor import process_items

def test_correctness():
    assert process_items(["a", "b", "c"]) == ["processed:a", "processed:b", "processed:c"]

def test_batch_performance():
    items = list(range(100))
    start = time.time()
    process_items(items)
    elapsed = time.time() - start
    assert elapsed < 0.2  # 100 items in batches of 10 = ~10 calls * 0.01s = 0.1s

def test_empty():
    assert process_items([]) == []
""",
    },
    hint="""# Solution
Chunk items into groups of 10, call `_slow_batch_call` per chunk, flatten results.""",
)


# ============================================================
# TASK 42: Reduce memory allocation
# ============================================================
write_task(
    42, "reduce_memory",
    prompt="""The `read_large_file(path)` function in `reader.py` reads the entire file into memory. Rewrite it as a generator that yields one line at a time.

All tests in `test_reader.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_reader.py -q""",
    repo_files={
        "reader.py": """def read_large_file(path):
    \"\"\"Read entire file into memory.\"\"\"
    with open(path, "r") as f:
        return f.readlines()
""",
        "test_reader.py": """import tempfile
from reader import read_large_file

def test_generator():
    # Verify it returns a generator-like iterable
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\\nline2\\nline3\\n")
        path = f.name

    result = read_large_file(path)
    # Should be iterable, not a list
    assert not isinstance(result, list)
    lines = list(result)
    assert lines == ["line1\\n", "line2\\n", "line3\\n"]
""",
    },
    hint="""# Solution
Use `yield from f` or `for line in f: yield line` to make it a generator.""",
)


# ============================================================
# TASK 43: Optimize algorithmic complexity
# ============================================================
write_task(
    43, "optimize_algo",
    prompt="""The `has_pair_sum(nums, target)` function in `pairs.py` is O(n²). Rewrite it to be O(n) average case using a set/hash map.

All tests in `test_pairs.py` must pass, including a stress test.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_pairs.py -q""",
    repo_files={
        "pairs.py": """def has_pair_sum(nums, target):
    \"\"\"Return True if any two distinct numbers sum to target. O(n²).\"\"\"
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            if nums[i] + nums[j] == target:
                return True
    return False
""",
        "test_pairs.py": """import time
from pairs import has_pair_sum

def test_basic():
    assert has_pair_sum([1, 2, 3, 4], 7) is True  # 3+4

def test_no_pair():
    assert has_pair_sum([1, 2, 3], 10) is False

def test_same_element():
    assert has_pair_sum([3, 3], 6) is True

def test_stress():
    nums = list(range(100000))
    start = time.time()
    assert has_pair_sum(nums, 199999) is True  # 99999 + 100000
    elapsed = time.time() - start
    assert elapsed < 0.5
""",
    },
    hint="""# Solution
Use a set: for each num, check if `target - num` is in the set, then add num to the set.""",
)


# ============================================================
# TASK 44: Write tests for untested code
# ============================================================
write_task(
    44, "write_tests",
    prompt="""The `calculator.py` module has functions with no tests. Write comprehensive tests in `test_calculator.py` covering:

- `add(a, b)` — basic addition, negative numbers, floats
- `divide(a, b)` — normal division, division by zero (should raise ValueError)
- `mean(values)` — normal case, empty list (should raise ValueError)

Use `pytest`. All tests must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_calculator.py -q""",
    repo_files={
        "calculator.py": """def add(a, b):
    return a + b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b

def mean(values):
    if not values:
        raise ValueError("Empty list")
    return sum(values) / len(values)
""",
        "test_calculator.py": """# TODO: write tests for calculator.py
""",
    },
    hint="""# Solution
Write pytest tests for each function covering happy paths and edge cases.""",
)


# ============================================================
# TASK 45: Fix flaky tests
# ============================================================
write_task(
    45, "fix_flaky",
    prompt="""The `test_timer.py` tests are flaky because they depend on actual time passing. Replace the real `time.sleep` with a mock or inject a clock dependency so tests are deterministic and fast.

All tests in `test_timer.py` must pass quickly (< 0.1s total).""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_timer.py -q""",
    repo_files={
        "timer.py": """import time

def wait_for(callback, timeout=5):
    \"\"\"Wait up to timeout seconds for callback to return True.\"\"\"
    start = time.time()
    while time.time() - start < timeout:
        if callback():
            return True
        time.sleep(0.1)
    return False
""",
        "test_timer.py": """import time
from timer import wait_for

def test_wait_success():
    start = time.time()
    result = wait_for(lambda: True, timeout=1)
    assert result is True
    assert time.time() - start < 0.2

def test_wait_timeout():
    start = time.time()
    result = wait_for(lambda: False, timeout=0.5)
    assert result is False
    assert time.time() - start < 0.7
""",
    },
    hint="""# Solution
Add a `clock` parameter defaulting to `time.time`, or use `monkeypatch` in tests to mock time.""",
)


# ============================================================
# TASK 46: Add edge case tests
# ============================================================
write_task(
    46, "edge_case_tests",
    prompt="""The `parse_date(text)` function in `dates.py` has basic tests but misses edge cases. Add tests in `test_dates.py` for:

- Empty string (should return None)
- Invalid format (should return None)
- February 29 on leap year vs non-leap year
- Single-digit month/day

All existing and new tests must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_dates.py -q""",
    repo_files={
        "dates.py": """from datetime import datetime

def parse_date(text):
    \"\"\"Parse YYYY-MM-DD. Return None on failure.\"\"\"
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
""",
        "test_dates.py": """from dates import parse_date
from datetime import date

def test_valid():
    assert parse_date("2024-01-15") == date(2024, 1, 15)

# TODO: add edge case tests
""",
    },
    hint="""# Solution
Add tests for empty string, invalid format, leap year Feb 29, non-leap year Feb 29, single-digit month/day.""",
)


# ============================================================
# TASK 47: Mock external dependency
# ============================================================
write_task(
    47, "mock_external",
    prompt="""The `weather.py` module calls an external API that is unavailable during tests. Modify the code to accept an optional `client` parameter for dependency injection, and write tests in `test_weather.py` that pass a mock client.

All tests in `test_weather.py` must pass without network access.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_weather.py -q""",
    repo_files={
        "weather.py": """import urllib.request
import json

def get_temperature(city):
    \"\"\"Fetch temperature from external API.\"\"\"
    url = f"https://api.example.com/weather?city={city}"
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read())
    return data["temperature"]
""",
        "test_weather.py": """# TODO: write tests with mock client
""",
    },
    hint="""# Solution
Add `client=None` parameter, if None use `urllib.request.urlopen`, else call `client.open(url)`. In tests, pass a mock object with `.open()` returning a context manager with `.read()` returning JSON.""",
)


# ============================================================
# TASK 48: Fix DOM manipulation
# ============================================================
write_task(
    48, "dom_bug",
    prompt="""The `toggle.js` module has a `toggleClass(element, className)` function that should add the class if missing, remove it if present. It currently always adds the class, causing duplicates.

Fix it so all tests in `toggle.test.mjs` pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test toggle.test.mjs""",
    repo_files={
        "toggle.js": """export function toggleClass(element, className) {
  // Bug: always adds, never removes
  element.classList.add(className);
}
""",
        "toggle.test.mjs": """import { toggleClass } from "./toggle.js";
import assert from "node:assert";

function mockElement(classes = []) {
  return {
    classList: {
      _classes: new Set(classes),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
    },
  };
}

{
  const el = mockElement(["active"]);
  toggleClass(el, "active");
  assert.strictEqual(el.classList.contains("active"), false);
}

{
  const el = mockElement([]);
  toggleClass(el, "active");
  assert.strictEqual(el.classList.contains("active"), true);
}
""",
    },
    hint="""# Solution
Use `element.classList.toggle(className)` or check `contains` and conditionally add/remove.""",
)


# ============================================================
# TASK 49: Fix event handling
# ============================================================
write_task(
    49, "event_handling",
    prompt="""The `form.js` module validates a form on submit but prevents the default action incorrectly, causing the form to submit even when invalid.

Fix the event handling so the form only submits when validation passes. All tests in `form.test.mjs` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test form.test.mjs""",
    repo_files={
        "form.js": """export function setupForm(form) {
  form.addEventListener("submit", (event) => {
    const email = form.querySelector("[name=email]").value;
    if (!email.includes("@")) {
      return false;  // Bug: doesn't prevent default
    }
    return true;
  });
}
""",
        "form.test.mjs": """import { setupForm } from "./form.js";
import assert from "node:assert";

function mockForm(emailValue) {
  let prevented = false;
  let submitted = false;
  const listeners = {};
  return {
    querySelector(sel) {
      return { value: emailValue };
    },
    addEventListener(event, handler) {
      listeners[event] = handler;
    },
    submit() {
      submitted = true;
      if (listeners.submit) {
        const evt = { preventDefault() { prevented = true; } };
        const result = listeners.submit(evt);
        // If preventDefault was called, don't submit
        if (prevented) submitted = false;
      }
    },
    get submitted() { return submitted; },
  };
}

{
  const form = mockForm("bad");
  setupForm(form);
  form.submit();
  assert.strictEqual(form.submitted, false, "invalid form should not submit");
}

{
  const form = mockForm("good@example.com");
  setupForm(form);
  form.submit();
  assert.strictEqual(form.submitted, true, "valid form should submit");
}
""",
    },
    hint="""# Solution
Call `event.preventDefault()` when validation fails, not just `return false`.""",
)


# ============================================================
# TASK 50: Fix CSS-in-JS
# ============================================================
write_task(
    50, "css_in_js",
    prompt="""The `styles.js` module generates CSS but forgets to add units to numeric values (e.g., `width: 100` instead of `width: 100px`).

Fix it so pixel values get `px` appended, and percentage values keep the `%`. All tests in `styles.test.mjs` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test styles.test.mjs""",
    repo_files={
        "styles.js": """export function toCSS(styleObj) {
  const rules = [];
  for (const [prop, value] of Object.entries(styleObj)) {
    const kebab = prop.replace(/([A-Z])/g, "-$1").toLowerCase();
    rules.push(`${kebab}: ${value}`);
  }
  return rules.join("; ");
}
""",
        "styles.test.mjs": """import { toCSS } from "./styles.js";
import assert from "node:assert";

{
  const css = toCSS({ width: 100, height: "50%" });
  assert.ok(css.includes("width: 100px"), `got: ${css}`);
  assert.ok(css.includes("height: 50%"), `got: ${css}`);
}

{
  const css = toCSS({ marginTop: 10 });
  assert.ok(css.includes("margin-top: 10px"), `got: ${css}`);
}
""",
    },
    hint="""# Solution
Check if value is a number (not string), append `px`. Keep strings as-is.""",
)


# ============================================================
# TASK 51: Fix async data loading
# ============================================================
write_task(
    51, "async_data",
    prompt="""The `loader.js` module fetches data but doesn't handle the case where the response is not JSON, causing an unhandled rejection.

Add proper error handling: if JSON parsing fails, return `{error: "Invalid JSON"}`. All tests in `loader.test.mjs` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
node --test loader.test.mjs""",
    repo_files={
        "loader.js": """export async function loadData(url) {
  const response = await fetch(url);
  const data = await response.json();  // Bug: no error handling
  return data;
}
""",
        "loader.test.mjs": """import { loadData } from "./loader.js";
import assert from "node:assert";

// Mock fetch
global.fetch = async (url) => {
  if (url === "/good") {
    return { json: async () => ({ ok: true }) };
  }
  if (url === "/bad") {
    return { json: async () => { throw new Error("not json"); } };
  }
  throw new Error("network");
};

{
  const data = await loadData("/good");
  assert.deepStrictEqual(data, { ok: true });
}

{
  const data = await loadData("/bad");
  assert.deepStrictEqual(data, { error: "Invalid JSON" });
}
""",
    },
    hint="""# Solution
Wrap `response.json()` in try/catch, return `{error: "Invalid JSON"}` on failure.""",
)


# ============================================================
# TASK 52: Fix injection vulnerability
# ============================================================
write_task(
    52, "fix_injection",
    prompt="""The `query.py` module builds SQL queries with string formatting, making it vulnerable to SQL injection.

Rewrite `find_user(name)` to use parameterized queries. All tests in `test_query.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_query.py -q""",
    repo_files={
        "query.py": """import sqlite3

def find_user(name):
    \"\"\"Find user by name — VULNERABLE to SQL injection.\"\"\"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.execute("INSERT INTO users VALUES (2, 'Bob')")
    # Vulnerable:
    cursor = conn.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cursor.fetchall()
""",
        "test_query.py": """from query import find_user

def test_normal():
    assert find_user("Alice") == [(1, "Alice")]

def test_injection_blocked():
    # This should NOT return all rows
    result = find_user("' OR '1'='1")
    assert len(result) == 0  # no user with that literal name
""",
    },
    hint="""# Solution
Use parameterized query: `conn.execute("SELECT * FROM users WHERE name = ?", (name,))`.""",
)


# ============================================================
# TASK 53: Fix authentication bypass
# ============================================================
write_task(
    53, "auth_bypass",
    prompt="""The `check_auth(token)` function in `auth.py` has a timing attack vulnerability: it returns `True` early if the token starts with the same prefix, leaking information about valid tokens.

Fix it to use constant-time comparison. All tests in `test_auth.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_auth.py -q""",
    repo_files={
        "auth.py": """SECRET = "supersecrettoken123"

def check_auth(token):
    \"\"\"Check token — vulnerable to timing attack.\"\"\"
    if len(token) != len(SECRET):
        return False
    for i in range(len(token)):
        if token[i] != SECRET[i]:
            return False  # Early return leaks info
    return True
""",
        "test_auth.py": """from auth import check_auth

def test_valid():
    assert check_auth("supersecrettoken123") is True

def test_invalid():
    assert check_auth("wrongtoken") is False

def test_wrong_length():
    assert check_auth("supersecrettoken12") is False
""",
    },
    hint="""# Solution
Use `hmac.compare_digest(token, SECRET)` for constant-time comparison.""",
)


# ============================================================
# TASK 54: Fix path traversal
# ============================================================
write_task(
    54, "path_traversal",
    prompt="""The `serve_file(base_dir, filename)` function in `server.py` is vulnerable to path traversal: `filename="../../etc/passwd"` escapes `base_dir`.

Fix it to reject any path that escapes `base_dir`. All tests in `test_server.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_server.py -q""",
    repo_files={
        "server.py": """from pathlib import Path

def serve_file(base_dir, filename):
    \"\"\"Read file from base_dir. VULNERABLE to path traversal.\"\"\"
    path = Path(base_dir) / filename
    return path.read_text()
""",
        "test_server.py": """import pytest
from server import serve_file

def test_normal():
    # This test assumes base_dir exists with a file
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "test.txt").write_text("hello")
        assert serve_file(td, "test.txt") == "hello"

def test_traversal_blocked():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "secret.txt").write_text("secret")
        outside = Path(td).parent
        with pytest.raises(ValueError):
            serve_file(str(outside / "safe"), "../secret.txt")
""",
    },
    hint="""# Solution
Resolve the path and check `path.resolve().startswith(Path(base_dir).resolve())`, or use `os.path.commonpath`.""",
)


# ============================================================
# TASK 55: Add input validation
# ============================================================
write_task(
    55, "input_validation",
    prompt="""The `create_user(data)` function in `users.py` accepts any dict without validation. Add validation:

- `name` is required, must be a non-empty string
- `age` is required, must be an integer 0-150
- `email` is optional, but if present must contain "@"
- Raise `ValueError` with descriptive messages

All tests in `test_users.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_users.py -q""",
    repo_files={
        "users.py": """def create_user(data):
    \"\"\"Create user from dict — no validation currently.\"\"\"
    return {
        "name": data.get("name"),
        "age": data.get("age"),
        "email": data.get("email"),
    }
""",
        "test_users.py": """import pytest
from users import create_user

def test_valid():
    u = create_user({"name": "Alice", "age": 30})
    assert u["name"] == "Alice"

def test_missing_name():
    with pytest.raises(ValueError, match="name"):
        create_user({"age": 30})

def test_empty_name():
    with pytest.raises(ValueError, match="name"):
        create_user({"name": "", "age": 30})

def test_invalid_age():
    with pytest.raises(ValueError, match="age"):
        create_user({"name": "Alice", "age": 200})

def test_invalid_email():
    with pytest.raises(ValueError, match="email"):
        create_user({"name": "Alice", "age": 30, "email": "bad"})

def test_optional_email():
    u = create_user({"name": "Alice", "age": 30})
    assert "email" not in u
""",
    },
    hint="""# Solution
Add validation checks for each field, raise ValueError with descriptive messages, only include email if provided and valid.""",
)


# ============================================================
# TASK 56: Fix type coercion bug
# ============================================================
write_task(
    56, "type_coercion",
    prompt="""The `sum_ids(items)` function in `ids.py` sums `id` fields but coerces strings to integers unexpectedly because it uses `int()` on everything.

Fix it to only accept actual integers, raising `TypeError` for non-int `id` values. All tests in `test_ids.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_ids.py -q""",
    repo_files={
        "ids.py": """def sum_ids(items):
    \"\"\"Sum the 'id' fields of all items.\"\"\"
    total = 0
    for item in items:
        total += int(item["id"])  # Bug: coerces strings
    return total
""",
        "test_ids.py": """import pytest
from ids import sum_ids

def test_integers():
    assert sum_ids([{"id": 1}, {"id": 2}]) == 3

def test_string_rejected():
    with pytest.raises(TypeError):
        sum_ids([{"id": "1"}])

def test_float_rejected():
    with pytest.raises(TypeError):
        sum_ids([{"id": 1.5}])

def test_empty():
    assert sum_ids([]) == 0
""",
    },
    hint="""# Solution
Check `isinstance(item["id"], int)` before adding, raise TypeError if not.""",
)


# ============================================================
# TASK 57: Add schema validation
# ============================================================
write_task(
    57, "schema_validation",
    prompt="""The `process_order(raw)` function in `orders.py` assumes the input dict has the right shape. Add validation that checks:

- `raw` is a dict
- `items` is a list of dicts, each with `name` (str) and `qty` (int > 0)
- `customer` is a dict with `name` (str)
- Raise `ValueError` for any violation

All tests in `test_orders.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_orders.py -q""",
    repo_files={
        "orders.py": """def process_order(raw):
    \"\"\"Process raw order dict. No validation currently.\"\"\"
    total = sum(item["qty"] * item.get("price", 0) for item in raw["items"])
    return {
        "customer": raw["customer"]["name"],
        "total": total,
    }
""",
        "test_orders.py": """import pytest
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
""",
    },
    hint="""# Solution
Add type/shape checks at the top of `process_order`, validate each item has name (str) and qty (int > 0), validate customer has name (str).""",
)


# ============================================================
# TASK 58: Fix async/await bug
# ============================================================
write_task(
    58, "async_bug",
    prompt="""The `fetch_all(urls)` function in `fetcher.py` is supposed to fetch URLs concurrently but currently awaits them sequentially.

Fix it to use `asyncio.gather` for true concurrency. All tests in `test_fetcher.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_fetcher.py -q""",
    repo_files={
        "fetcher.py": """import asyncio

async def _fetch(url):
    await asyncio.sleep(0.01)
    return f"data:{url}"

async def fetch_all(urls):
    \"\"\"Fetch all URLs — currently sequential.\"\"\"
    results = []
    for url in urls:
        results.append(await _fetch(url))  # sequential
    return results
""",
        "test_fetcher.py": """import asyncio
import time
from fetcher import fetch_all

def test_concurrent():
    urls = ["a", "b", "c"]
    start = time.time()
    result = asyncio.run(fetch_all(urls))
    elapsed = time.time() - start
    assert elapsed < 0.03  # 3 concurrent * 0.01s = ~0.01s, not 0.03s
    assert result == ["data:a", "data:b", "data:c"]
""",
    },
    hint="""# Solution
Use `asyncio.gather(*[_fetch(url) for url in urls])` instead of sequential await.""",
)


# ============================================================
# TASK 59: Add async error handling
# ============================================================
write_task(
    59, "async_errors",
    prompt="""The `safe_fetch(url)` function in `fetcher.py` doesn't handle exceptions from `_fetch`. Add try/except so it returns `{"error": str(e)}` on failure instead of crashing.

All tests in `test_fetcher.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_fetcher.py -q""",
    repo_files={
        "fetcher.py": """import asyncio

async def _fetch(url):
    if "fail" in url:
        raise ConnectionError("Network down")
    await asyncio.sleep(0.01)
    return f"data:{url}"

async def safe_fetch(url):
    \"\"\"Fetch with no error handling.\"\"\"
    return await _fetch(url)
""",
        "test_fetcher.py": """import asyncio
from fetcher import safe_fetch

def test_success():
    result = asyncio.run(safe_fetch("ok"))
    assert result == "data:ok"

def test_failure():
    result = asyncio.run(safe_fetch("fail"))
    assert result == {"error": "Network down"}
""",
    },
    hint="""# Solution
Wrap `await _fetch(url)` in try/except, return `{"error": str(e)}` on exception.""",
)


# ============================================================
# TASK 60: Fix resource leak in async
# ============================================================
write_task(
    60, "async_resource_leak",
    prompt="""The `stream_lines(path)` async generator in `reader.py` opens a file but never closes it, leaking file descriptors.

Fix it to properly close the file when done. All tests in `test_reader.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_reader.py -q""",
    repo_files={
        "reader.py": """async def stream_lines(path):
    \"\"\"Stream lines from file — leaks descriptor.\"\"\"
    f = open(path, "r")
    for line in f:
        yield line.strip()
    # Missing: f.close()
""",
        "test_reader.py": """import asyncio
import tempfile
from reader import stream_lines

def test_reads_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("a\\nb\\nc\\n")
        path = f.name

    async def collect():
        return [line async for line in stream_lines(path)]

    result = asyncio.run(collect())
    assert result == ["a", "b", "c"]
""",
    },
    hint="""# Solution
Use `async with aiofiles.open(path) as f:` or `try/finally` with `f.close()`.""",
)


# ============================================================
# TASK 61: Fix CSV parsing
# ============================================================
write_task(
    61, "csv_parse",
    prompt="""The `parse_csv(text)` function in `csv_parser.py` splits on commas but doesn't handle quoted fields containing commas.

Fix it to properly handle quoted fields. All tests in `test_csv_parser.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_csv_parser.py -q""",
    repo_files={
        "csv_parser.py": """def parse_csv(text):
    \"\"\"Parse CSV text into list of lists. Doesn't handle quotes.\"\"\"
    rows = []
    for line in text.strip().split("\\n"):
        rows.append(line.split(","))
    return rows
""",
        "test_csv_parser.py": """from csv_parser import parse_csv

def test_simple():
    assert parse_csv("a,b,c\\n1,2,3") == [["a", "b", "c"], ["1", "2", "3"]]

def test_quoted_comma():
    assert parse_csv('a,"b,c",d\\n1,2,3') == [["a", "b,c", "d"], ["1", "2", "3"]]

def test_quoted_newline():
    assert parse_csv('a,"b\\nc",d') == [["a", "b\\nc", "d"]]
""",
    },
    hint="""# Solution
Use the `csv` module's `csv.reader` which handles quotes correctly, or implement a simple state machine.""",
)


# ============================================================
# TASK 62: Fix data transformation pipeline
# ============================================================
write_task(
    62, "data_pipeline",
    prompt="""The `transform(records)` function in `pipeline.py` should uppercase names and round prices to 2 decimals, but it mutates the input dicts in place.

Fix it to return new dicts without modifying the inputs. All tests in `test_pipeline.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_pipeline.py -q""",
    repo_files={
        "pipeline.py": """def transform(records):
    \"\"\"Transform records — currently mutates in place.\"\"\"
    for record in records:
        record["name"] = record["name"].upper()
        record["price"] = round(record["price"], 2)
    return records
""",
        "test_pipeline.py": """from pipeline import transform

def test_transform():
    records = [{"name": "alice", "price": 10.555}]
    result = transform(records)
    assert result == [{"name": "ALICE", "price": 10.56}]

def test_no_mutation():
    original = [{"name": "alice", "price": 10.555}]
    transform(original)
    assert original == [{"name": "alice", "price": 10.555}]
""",
    },
    hint="""# Solution
Build new dicts instead of modifying in place: `[{**r, "name": r["name"].upper(), "price": round(r["price"], 2)} for r in records]`.""",
)


# ============================================================
# TASK 63: Add data aggregation
# ============================================================
write_task(
    63, "data_aggregation",
    prompt="""The `summarize_sales(records)` function in `sales.py` only returns total revenue. Extend it to also return:

- `total_revenue`: sum of all `amount`
- `average_order`: total / number of orders
- `top_customer`: customer with highest total spending
- `by_category`: dict mapping category to total revenue

All tests in `test_sales.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_sales.py -q""",
    repo_files={
        "sales.py": """def summarize_sales(records):
    \"\"\"Return total revenue only.\"\"\"
    return {"total_revenue": sum(r["amount"] for r in records)}
""",
        "test_sales.py": """from sales import summarize_sales

RECORDS = [
    {"order_id": 1, "customer": "Alice", "category": "electronics", "amount": 100},
    {"order_id": 2, "customer": "Bob", "category": "clothing", "amount": 50},
    {"order_id": 3, "customer": "Alice", "category": "electronics", "amount": 200},
]

def test_total():
    assert summarize_sales(RECORDS)["total_revenue"] == 350

def test_average():
    assert summarize_sales(RECORDS)["average_order"] == 350 / 3

def test_top_customer():
    assert summarize_sales(RECORDS)["top_customer"] == "Alice"

def test_by_category():
    assert summarize_sales(RECORDS)["by_category"] == {"electronics": 300, "clothing": 50}

def test_empty():
    assert summarize_sales([]) == {"total_revenue": 0, "average_order": 0, "top_customer": None, "by_category": {}}
""",
    },
    hint="""# Solution
Compute all aggregates in one pass: total, count, customer totals (dict), category totals (dict).""",
)


# ============================================================
# TASK 64: Fix encoding issues
# ============================================================
write_task(
    64, "encoding_fix",
    prompt="""The `read_manifest(path)` function in `manifest.py` reads a UTF-8 file but returns bytes on Python 3 because it opens in binary mode.

Fix it to return a string and properly decode UTF-8. All tests in `test_manifest.py` must pass.""",
    test_sh="""#!/usr/bin/env bash
set -e
WORKDIR="${1:-./repo}"
cd "$WORKDIR"
python -m pytest test_manifest.py -q""",
    repo_files={
        "manifest.py": """def read_manifest(path):
    \"\"\"Read manifest file. Returns bytes by mistake.\"\"\"
    with open(path, "rb") as f:
        return f.read()
""",
        "test_manifest.py": """import tempfile
from manifest import read_manifest

def test_returns_string():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("hello")
        path = f.name
    result = read_manifest(path)
    assert isinstance(result, str)
    assert result == "hello"

def test_unicode():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("héllo wörld")
        path = f.name
    result = read_manifest(path)
    assert isinstance(result, str)
    assert "héllo" in result
""",
    },
    hint="""# Solution
Open in text mode: `open(path, "r", encoding="utf-8")` or decode after reading: `f.read().decode("utf-8")`.""",
)


if __name__ == "__main__":
    written = sorted(KEEP_TASKS)
    print(f"Generated {len(written)} tasks in {TASKS_DIR}")
    print(f"Kept task numbers: {written}")
