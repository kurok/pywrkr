# pywrkr

Async Python HTTP benchmarking tool (wrk/ab-inspired) with virtual user simulation, rate limiting, traffic profiles, distributed mode, and observability export.

## Tech Stack

- Python 3.11+, async/await with aiohttp
- Project packaging via `pyproject.toml` (src layout: `src/pywrkr/`)
- Optional extras: `[tui]`, `[otel]`, `[http2]`, `[pytest]`, `[dev]`, `[lint]`, `[security]`, `[all]`

## Commands

```bash
# Install (editable, with dev + lint deps)
uv pip install -e ".[dev,lint]"

# Run all tests (parallel via pytest-xdist)
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_pywrkr.py -v

# Run specific test class
uv run pytest tests/test_pywrkr.py::TestMakeUrl -v

# Sequential (useful for debugging)
uv run pytest tests/ -v -n 0

# Lint & format
uv run ruff format src/ tests/
uv run ruff check src/ tests/

# Type check
uv run python -m mypy src/pywrkr/ --ignore-missing-imports --disable-error-code import-untyped

# Security scan
uv run python -m bandit -r src/pywrkr/ -c pyproject.toml

# Pre-commit (all hooks)
uv run pre-commit run --all-files

# Build
uv build

# Run the tool directly
uv run python -m pywrkr http://localhost:8080/
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

- **No approving review** required (0 approvals since 2026-11-08); a PR can merge itself once checks are green
- **All review threads must be resolved** before merge
- **Required linear history** — squash or rebase merges only
- **Required status checks**: `test (ubuntu-latest, 3.14)` and `test (macos-latest, 3.15)` must pass
- **CodeQL** analysis must pass (high_or_higher security threshold)
- No force pushes or branch deletion on `main`
- Admin bypass is available for maintainers

### CI Pipeline (`python-package.yml`)

All jobs run on `push` to main and on every PR:

1. **lint** — ruff format check + ruff lint
2. **pre-commit** — trailing whitespace, EOF fixer, YAML/JSON check, ruff
3. **test** — each OS with the Python its users get: ubuntu-latest × 3.14 (Ubuntu 26.04 LTS python3), macos-latest × 3.15 (Homebrew python3 on macOS 27); coverage (85% threshold) from Ubuntu
4. **typecheck** — mypy with configured overrides
5. **security** — bandit + pip-audit
6. **test-otel** — the OTel export tests with the `[otel]` extra installed
7. **release-build** — builds the sdist/wheel from the hash-pinned `requirements-build.txt`
8. **all-checks-pass** — gate job that fails unless every job above succeeded

Separate workflows: `codeql.yml` (CodeQL, `security-and-quality` queries), `action-test.yml` (the GitHub Action), `docker-publish.yml`, `release.yml`/`publish.yml` (releases).

Coverage uploads to Codecov from the Ubuntu/3.14 job.

### Container image publishing

`.github/workflows/docker-publish.yml` builds the image and pushes it to **GitHub Container Registry**
(`ghcr.io/kurok/pywrkr`) and to **Docker Hub** (`docker.io/ryabikov/pywrkr`). It runs on a release,
on pushes to `main` that touch the image, and on `workflow_dispatch` (the optional `tag` input
publishes an extra tag). Pull requests build the image and check it, but push nothing, and the job
that pushes never runs for a pull request — so a pull request's Dockerfile is never built while a
write-scoped token is in reach.

The Docker Hub steps need two repository secrets — `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
(a Docker Hub access token with Read & Write on the repository). When they are absent the Docker Hub
steps are skipped and the GitHub Container Registry publish still happens, so a fork or a fresh
clone of the workflow keeps working.

An agent that needs the image can pull it (`docker pull docker.io/ryabikov/pywrkr:latest`) or ask
for a build with `gh workflow run docker-publish.yml --ref main -f tag=nightly`.

## Workflow: Creating a Clean PR

1. **Sync with main**: `git fetch origin main && git checkout main && git pull`
2. **Create branch**: `git checkout -b feature/descriptive-name` (use correct prefix!)
3. **Make changes** in `src/pywrkr/`, add/update tests in `tests/`
4. **Run all local checks before committing**:
   ```bash
   uv run ruff format src/ tests/
   uv run ruff check src/ tests/ --fix
   uv run pytest tests/ -v
   uv run python -m mypy src/pywrkr/ --ignore-missing-imports --disable-error-code import-untyped
   uv run pre-commit run --all-files
   ```
5. **Commit** using Conventional Commits: `feat: add X`, `fix: resolve Y`, `docs: update Z`
6. **Push**: `git push -u origin feature/descriptive-name`
7. **Create PR**: `gh pr create --title "type: description" --body "..."`
8. **Monitor CI**: `gh run list --branch feature/descriptive-name`
9. **Merge** once checks are green: `gh pr merge --squash --delete-branch` (auto-merge is enabled)

## Important Notes

- Never commit `.env` files or secrets
- The test suite uses real aiohttp test servers — tests may bind local ports
- `infra/` contains HCL/Terraform — do not modify without understanding the deployment context
- When adding new CLI flags, update both the argparse setup and the README usage table
- HAR import is a subcommand (`pywrkr har-import`) — keep it decoupled from core benchmarking logic
- Distributed mode uses TCP protocol on port 9220 — be careful with serialization changes
- Observability exports (OTel, Prometheus) are optional deps — guard imports with try/except
- When releasing, update `version` in `pyproject.toml` only — `__version__` is derived from installed package metadata via `importlib.metadata` (see #149), so there is no second location to update
- Release automation triggers on `v*` tags — verifies version matches pyproject.toml
- Timing-sensitive tests should assert on **total elapsed time**, not individual intervals (CI runners have jitter)
- pytest runs with `-n 7` by default (parallel) — tests must not depend on shared state
