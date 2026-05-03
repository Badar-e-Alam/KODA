# Solution sketch

The agent should write tests covering:
- `c_to_f`: 0->32, 100->212, -40->-40
- `f_to_c`: 32->0, 212->100
- `classify`: each branch (freezing / cold / mild / warm / hot)
- Boundary values: 0 (freezing), 0.01 (cold), 14.99 (cold), 15 (mild), etc.
- ValueError below absolute zero
- pytest.approx for float comparisons
