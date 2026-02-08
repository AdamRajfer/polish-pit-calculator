# Polish PIT Calculator

Tool for generating content for Polish PIT forms.

## 1. Installation

```bash
uv sync
```

## 2. Usage

### 2.1. Charles Schwab Employee Sponsored

1. Go to https://client.schwab.com/app/accounts/transactionhistory/#/.
2. Select the Employee Sponsored account for which you want to generate the report.
3. Select "Previous 4 Years" in Data Range field and press Search.
4. Press Export at the top-right side of the page in order to download the CSV file.
5. Run:

```python
from pathlib import Path
from polish_pit_calculator.schwab import SchwabEmployeeSponsoredTaxReporter

report_path = "PATH_TO_YOUR_REPORT.csv"
tax_reporter = SchwabEmployeeSponsoredTaxReporter(report_path)
tax_report = tax_reporter.generate()
```
