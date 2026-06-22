"""SWE-bench harness helpers (split picking, etc.).

The actual runner lives in ``eval/swebench_runner.py``; this package
exists for the dev-split picker and any future SWE-bench-specific
utilities that don't belong inside the generic runner.

This directory shares the ``swebench`` name with the pip-installed
SWE-bench harness. Without the line below, importing this local package
(first on ``sys.path`` when running from ``koda-evals/``) would *shadow*
the installed one, so ``swebench.harness.run_evaluation`` — needed for
grading — would be unimportable. ``extend_path`` merges this directory's
search path with the installed package's, so local modules
(``pick_dev_split``) and harness modules (``harness.*``) both resolve.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
