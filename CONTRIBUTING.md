# Contributing to KODA

Thanks for your interest! KODA is a small project so the process is informal.

## Dev setup

```bash
git clone https://github.com/<you>/KODA.git
cd KODA
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\activate
pip install -e ".[all]"
cp .env.example .env  # add at least one provider key
```

## Running tests

```bash
pytest tests/
```

Tests use Textual's `run_test()` pilot for async UI testing.

## Code style

- Python 3.11+ syntax (`str | None`, `list[str]`, etc.)
- Type hints on public functions
- `from __future__ import annotations` at the top of every module
- Docstrings on public functions; keep them one-line where possible
- No trailing whitespace, LF line endings

## Pull requests

1. Fork, branch off `main`
2. Keep PRs focused — one change per PR is easier to review
3. Add or update a test if you change behavior
4. Run `pytest` locally before pushing
5. Open a PR with a clear description of *why* the change is needed

## Reporting bugs

Open a GitHub issue with:
- OS + Python version
- KODA version (`koda --version`)
- Steps to reproduce
- What you expected vs what happened

## Security issues

See [SECURITY.md](SECURITY.md) — please do not open a public issue for
security reports.
