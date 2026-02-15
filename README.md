# Polish PIT Calculator

Tool for generating content for Polish PIT forms.

## 1. Installation

```bash
uv sync
```

## 2. Usage

Run the console app:

```bash
uv run pit-pl
```

App flow:

1. Select `Submit tax report`.
2. Pick one report type and provide one source:
   - file reporters: select a `.csv` file
   - IB Flex Query: provide `Query ID` and API token
3. Repeat `Submit tax report` until all sources are added.
4. Select `Prepare tax summary`.
5. Wait for loader (`Preparing tax summary...`).
6. Review the final tax summary table.
7. Select `Start over` or `Exit`.

Notes:

1. Press `Esc` in report/file/token prompts to go back.
2. Press `Ctrl + C` at any time to exit immediately.
