# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.1.0] - 2026-02-23

### Added

- New manual reporters for yearly PIT inputs: `Trade`, `Crypto`, and `Employment`.
- Structured log model via `TaxReportLogs` and `LogChange`, with chronological ordering by
  transaction date.
- Reporter prompt validator reuse via shared validators in `polish_pit_calculator/validators.py`.
- Main-menu action `Reset tax report`.
- Package layout migrated from `src/*` modules to the package namespace
  `polish_pit_calculator/*`.
- Reporter implementations moved under `polish_pit_calculator/tax_reporters/`.
- App responsibilities split into focused modules:
  `app.py` (flow), `ui.py` (terminal/prompt), `registry.py` (persistence), `caches.py` (FX cache).
- Reporter registry serialization now uses canonical class paths:
  `polish_pit_calculator.tax_reporters.<ClassName>`.
- Reporter naming updated to current class/display names:
  `IBKRTaxReporter`, `CharlesSchwabEmployeeSponsoredTaxReporter`,
  and display names shown in registry/menu.
- Skill and repository guidance updated to reflect current paths, reporters, and workflows.
- Reporter set is now focused on supported sources and manual reporters (no `Raw` reporter).
- Maintained guidance files now consistently reference package paths instead of legacy `src/*`.
- `TaxReport.__radd__` now handles subclass dispatch fallback without recursion.
- Report/log presentation flow is consistent across prepare/show/back cycles.
