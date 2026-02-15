# Contributing

## Setup

```bash
uv sync --group dev
```

## Workflow

1. Create a feature branch from `main`
2. Make focused changes with tests
3. Run checks locally:

```bash
uv run pytest
pre-commit run --all-files
```

4. Commit with a clear message
5. Open a pull request

## Coding Standards

- Keep line length at `100`
- Prefer explicit tests for new behavior
- Avoid adding secrets or personal data to source/test fixtures
- Keep public behavior backward-compatible unless change is intentional
- Do not commit credentials, private keys, or API tokens

## Pull Request Checklist

- [ ] Tests added/updated
- [ ] `uv run pytest` passes
- [ ] `pre-commit run --all-files` passes
- [ ] Docs updated when behavior changed
