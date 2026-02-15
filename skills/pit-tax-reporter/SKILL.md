---
name: pit-tax-reporter
description: Implement and refactor Polish PIT tax reporters and related parsing/aggregation logic. Use when adding or modifying reporters in src/, normalizing broker CSV/XML input, adjusting exchange-rate usage/caching, or updating tests to assert full report outputs.
---

# Pit Tax Reporter

## Overview

Build reporter changes safely by preserving tax semantics and verifying full outputs.
Use this workflow for IB, IB Flex Query, Schwab, Revolut, Coinbase, Raw, and shared tax models.

## Workflow

1. Confirm target behavior before editing:
- Expected output is `TaxReport` and `TaxRecord` values by year.
- Required CLI/report table behavior if app flow is touched.
2. Normalize inputs deterministically:
- Parse dates, currencies, and numeric values explicitly.
- Handle malformed rows by dropping/raising consistently.
3. Keep financial semantics explicit:
- Preserve FIFO matching and per-year aggregation rules.
- Keep exchange-rate lookups and cache behavior consistent.
4. Prefer focused refactors:
- Reduce duplication without changing external behavior.
- Keep reporter responsibilities clear and testable.
5. Test with full-output assertions:
- Compare full dataframes/tax reports, not selected fields.
- Mock cross-module dependencies when unit-testing a module.
6. Run quality gates:
- `uv run pytest -q`
- `pre-commit run --all-files`

## Output Standard

Provide:
- Behavior summary.
- Exact files changed.
- Test evidence (`pytest` + `pre-commit` results).
- Any assumptions or unresolved data-contract questions.

## References

Use `references/reporter-checklist.md` for implementation and testing checks.
