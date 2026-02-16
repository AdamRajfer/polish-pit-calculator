# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.1.0] - 2026-02-15

### Added

- Initial public release of Polish PIT Calculator.
- Registry-first interactive console flow:
  `Register tax reporter`, `List tax reporters`, `Remove tax reporters`, `Prepare tax report`,
  `Show tax report`, `Exit`.
- Reporter registration for file-based sources and Interactive Brokers Trade Cash API credentials.
- Persistent reporter registry for cross-session workflows.
- Report preparation loader with immediate summary display and dedicated re-show action.
- Framed traceback output for report-preparation failures (`[esc to back]` flow).
- Exchange-rate caching for previous years.
- Expanded test suite across reporters, CLI/app flows, and cache utilities.
- Coverage reporting in default `pytest` runs.
- Repository docs: `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`.
