# task_12_frontend_dom — solution hint

The bug is in `cart.js::cartTotal`. The current code accumulates `pct +=
d.percent` over every applicable discount; it should pick a single
maximum:

```js
const pct = applicable.length
  ? Math.max(...applicable.map(d => d.percent))
  : 0;
```

Frontend coverage characteristic of this task:
- Pure JavaScript (ES modules), no npm install / no toolchain.
- Uses Node.js's built-in `node:test` runner (Node ≥18).
- The agent must use `read_file` / `edit_file` plus `run_shell node
  --test cart.test.mjs` to verify.
