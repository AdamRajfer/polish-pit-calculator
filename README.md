# Polish PIT Calculator

CLI package for aggregating broker/export data into a Polish PIT tax summary.

Current release: `0.1.0`

## Requirements

- Python `>=3.12`
- `uv` for dependency and environment management

## Installation

```bash
uv sync --group dev
```

## Run The App

```bash
uv run pit-pl
```

App flow:

1. `Submit tax report`
2. Select report type and provide one source
3. Repeat until all sources are submitted
4. `Prepare tax summary`
5. Review the summary table

Prompt controls:

- `Esc` goes back in selection/input prompts
- `Ctrl+C` exits immediately

## Development

Run tests (coverage is enabled by default):

```bash
uv run pytest
```

Run all quality hooks:

```bash
pre-commit run --all-files
```

## Coverage

Coverage report is printed at the end of `uv run pytest`.

## Versioning

Package version is derived from git tags via `setuptools-scm`.

Release tags should follow semantic versioning, for example:

- `v0.1.0`
- `v0.2.0`
- `v0.2.1`

## Project Docs

- `CONTRIBUTING.md`
- `SECURITY.md`
- `CHANGELOG.md`
- `LICENSE`
- `CODE_OF_CONDUCT.md`
