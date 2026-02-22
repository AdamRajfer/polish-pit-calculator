---
name: pit-tax-reporter
description: Implement and refactor Polish PIT tax reporters and related parsing/aggregation logic. Use when adding or modifying reporters in polish_pit_calculator/tax_reporters/, normalizing broker exports, adjusting exchange-rate usage/caching, or updating tests to assert full report outputs.
---

# Pit Tax Reporter

## Overview

Build reporter changes safely by preserving tax semantics and verifying full outputs.
Use this workflow for Charles Schwab, IBKR, Coinbase, Revolut, Trade, Crypto, Employment,
and shared tax models.

## Workflow

1. Confirm target behavior before editing:
- Expected output is `TaxReport` and `TaxRecord` values by year.
- Required CLI/report table behavior if app flow is touched.
2. Keep reporter contract stable:
- Reporter classes live in `polish_pit_calculator/tax_reporters/`.
- Keep `name()`, `validators()`, `to_entry_data()`, and `generate(logs)` coherent.
- Keep registry deserialization compatible with `polish_pit_calculator.tax_reporters.<ClassName>`.
3. Normalize inputs deterministically:
- Parse dates, currencies, and numeric values explicitly.
- Handle malformed rows by dropping/raising consistently.
4. Keep financial semantics explicit:
- Preserve FIFO matching and per-year aggregation rules.
- Keep exchange-rate lookups and cache behavior consistent.
5. Prefer focused refactors:
- Reduce duplication without changing external behavior.
- Keep reporter responsibilities clear and testable.
6. Test with full-output assertions:
- Compare full dataframes/tax reports, not selected fields.
- Mock cross-module dependencies when unit-testing a module.
7. Run quality gates:
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
