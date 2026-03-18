# pywrkr

Async Python HTTP benchmarking tool (wrk/ab-inspired) with virtual user simulation, rate limiting, traffic profiles, distributed mode, and observability export.

## Tech Stack

- Python 3.10+, async/await with aiohttp
- Project packaging via `pyproject.toml` (src layout: `src/pywrkr/`)
- Optional extras: `[tui]`, `[otel]`, `[dev]`, `[lint]`, `[security]`, `[all]`

## Commands

```bash
# Install (editable, with dev + lint deps)
pip install -e ".[dev,lint]"

# Run all tests (parallel via pytest-xdist)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_pywrkr.py -v

# Run specific test class
python -m pytest tests/test_pywrkr.py::TestMakeUrl -v

# Sequential (useful for debugging)
python -m pytest tests/ -v -n 0

# Lint & format
ruff format src/ tests/
ruff check src/ tests/

# Type check
python -m mypy src/pywrkr/ --ignore-missing-imports --disable-error-code import-untyped

# Security scan
python -m bandit -r src/pywrkr/ -c pyproject.toml

# Pre-commit (all hooks)
pre-commit run --all-files

# Build
python -m build

# Run the tool directly
python -m pywrkr http://localhost:8080/
```

## Architecture

- `src/pywrkr/` — main package (worker logic, stats, reporting, HAR import, distributed, observability)
- `tests/` — unit + integration tests (aiohttp test server, HAR import, reporting, distributed, multi-URL)
- `examples/` — usage examples and sample files
- `infra/` — infrastructure (Terraform/HCL)
- `Dockerfile` — container build

## Code Style

- Use `ruff` for both linting and formatting — run `ruff format` and `ruff check` before every commit
- Follow existing code conventions already in the repo
- Type hints on all new public functions and methods
- Docstrings for public API (Google style)
- Keep imports sorted (ruff handles this)
- Line length: 100 (configured in pyproject.toml)

## GitHub Repository Rules

### Branch Naming (Enforced by Ruleset)

Branches MUST use one of these prefixes — other names are **rejected** by GitHub:

- `feature/` — new features
- `fix/` — bug fixes
- `refactor/` — code refactoring
- `chore/` — maintenance tasks
- `docs/` — documentation changes
- `test/` — test additions/changes
- `ci/` — CI/CD changes
- `release/` — release preparation
- `hotfix/` — urgent production fixes

**IMPORTANT**: Do NOT use `feat/` — only `feature/` is allowed by the branch naming ruleset.

### Pull Request Requirements (Enforced by Ruleset)

- **1 approving review** required (dismiss stale reviews on new push)
- **Last push approval** required — the person who pushes last cannot self-approve
- **All review threads must be resolved** before merge
- **Required linear history** — squash or rebase merges only
- **Required status check**: `build (3.12)` must pass (NOTE: see CI section)
- **CodeQL** analysis must pass (high_or_higher security threshold)
- No force pushes or branch deletion on `main`
- Admin bypass is available for maintainers

### CI Pipeline (5 Jobs)

All jobs run on `push` to main and on every PR:

1. **lint** — ruff format check + ruff lint
2. **pre-commit** — trailing whitespace, EOF fixer, YAML/JSON check, ruff
3. **test** — matrix: Ubuntu + macOS × Python 3.10/3.11/3.12/3.13 with coverage (85% threshold)
4. **typecheck** — mypy with configured overrides
5. **security** — bandit + pip-audit

Coverage uploads to Codecov from Ubuntu/3.12 job.

### CI Status Check Note

The ruleset requires a status check named `build (3.12)` but the actual CI job is named `test (ubuntu-latest, 3.12)`. This mismatch means PRs currently require **admin bypass** to merge. To fix: update the ruleset required check to `test (ubuntu-latest, 3.12)`.

## Workflow: Creating a Clean PR

1. **Sync with main**: `git fetch origin main && git checkout main && git pull`
2. **Create branch**: `git checkout -b feature/descriptive-name` (use correct prefix!)
3. **Make changes** in `src/pywrkr/`, add/update tests in `tests/`
4. **Run all local checks before committing**:
   ```bash
   ruff format src/ tests/
   ruff check src/ tests/ --fix
   python -m pytest tests/ -v
   python -m mypy src/pywrkr/ --ignore-missing-imports --disable-error-code import-untyped
   pre-commit run --all-files
   ```
5. **Commit** using Conventional Commits: `feat: add X`, `fix: resolve Y`, `docs: update Z`
6. **Push**: `git push -u origin feature/descriptive-name`
7. **Create PR**: `gh pr create --title "type: description" --body "..."`
8. **Monitor CI**: `gh run list --branch feature/descriptive-name`
9. **Merge** (admin): `gh pr merge --admin --squash`

## Important Notes

- Never commit `.env` files or secrets
- The test suite uses real aiohttp test servers — tests may bind local ports
- `infra/` contains HCL/Terraform — do not modify without understanding the deployment context
- When adding new CLI flags, update both the argparse setup and the README usage table
- HAR import is a subcommand (`pywrkr har-import`) — keep it decoupled from core benchmarking logic
- Distributed mode uses TCP protocol on port 9220 — be careful with serialization changes
- Observability exports (OTel, Prometheus) are optional deps — guard imports with try/except
- When releasing, update version in both `pyproject.toml` and `src/pywrkr/__init__.py`
- Release automation triggers on `v*` tags — verifies version matches pyproject.toml
- Timing-sensitive tests should assert on **total elapsed time**, not individual intervals (CI runners have jitter)
- pytest runs with `-n 7` by default (parallel) — tests must not depend on shared state
