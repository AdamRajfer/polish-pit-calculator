# PIT Reporter Checklist

Use this checklist for any reporter/model/parser changes in this repository.

## Reporter Contract

1. Reporter returns `TaxReport` from `generate()`.
2. Year keys and values are deterministic.
3. `TaxRecord` fields map correctly to PIT rows and tax semantics.

## Input Parsing

1. Parse source rows with explicit typing:
- dates
- amounts
- currencies
2. Drop or reject invalid rows consistently.
3. Normalize description/symbol text only where required by matching logic.

## Financial Logic

1. Confirm buy/sell matching logic (FIFO where applicable).
2. Confirm commissions/fees inclusion is unchanged or intentionally updated.
3. Confirm withholding merges and sign handling.
4. Confirm exchange-rate lookup date semantics.
5. Confirm current-year cache rules and previous-year cache rules.

## Dataframe Outputs

1. Keep output columns stable for downstream aggregation.
2. Keep year derivation consistent across reporters.
3. Ensure empty-input behavior is explicit (`None`, empty df, or empty report).

## Testing Rules

1. Unit tests mock imported dependencies from other modules.
2. Assertions compare full outputs:
- full dataframe (`assert_frame_equal`)
- full `TaxReport`/`TaxRecord`
3. Avoid partial checks unless validating a specific error path.
4. Include failure/edge paths for parser and retry/error logic.

## Validation Commands

```bash
uv run pytest -q
pre-commit run --all-files
```
