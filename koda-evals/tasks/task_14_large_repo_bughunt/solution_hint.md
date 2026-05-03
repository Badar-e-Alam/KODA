# task_14_large_repo_bughunt — solution hint

The bug lives in `inventory_app/repository/discounts.py`:

```python
"SAVE10":  Discount(code="SAVE10",  percent=0.10, ...)  # ← bug: 0.10 should be 10
```

`Discount.percent` is documented as an integer on the 0-100 scale (see
`inventory_app/models/discount.py`). The repository stores `SAVE10` as
`0.10` — a fractional ratio — which makes `apply_discount` compute
`subtotal * (0.10 / 100)` ≈ `subtotal * 0.001`, almost no discount.

Fix: change `percent=0.10` to `percent=10` in
`inventory_app/repository/discounts.py`.

What this task probes:
- Tracing a failure across 4 layers (api → service → service → repo).
- Distinguishing the bug from ~17 distractor modules (analytics, audit,
  cache, retries, etc. — all plausible-looking but irrelevant).
- Total source is ~36 KB across 30+ files; sufficient to trigger the
  context compactor if the agent does many `read_file`s.
- The grader rejects test edits (sha256 check) so the agent has to fix
  the source, not silence the symptom.
