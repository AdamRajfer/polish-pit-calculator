"""Charles Schwab employee-sponsored CSV reporter implementation."""

from collections import defaultdict
from io import BytesIO

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import get_exchange_rate


class SchwabEmployeeSponsoredTaxReporter(TaxReporter):
    """Build tax report from Schwab employee-sponsored account exports."""

    def __init__(self, *csv_files: BytesIO) -> None:
        """Store Schwab CSV byte buffers."""
        self.csv_files = csv_files

    def generate(self) -> TaxReport:
        """Generate yearly tax records from normalized Schwab actions."""
        df = self._load_report()
        remaining: dict[str, list[pd.Series]] = defaultdict(list)
        tax_report = TaxReport()
        for _, row in df.iterrows():
            year = row["Date"].year
            exc_rate = get_exchange_rate(row["Currency"], row["Date"])
            if row["Action"] == "Deposit":
                for _ in range(int(row["Quantity"])):
                    remaining[row["Description"]].append(row)
            elif row["Action"] == "Sale":
                tax_record = TaxRecord(trade_cost=row["FeesAndCommissions"] * exc_rate)
                for _ in range(int(row["Shares"])):
                    sold_row = remaining[row["Type"]].pop(0)
                    sold_exc_rate = get_exchange_rate(sold_row["Currency"], sold_row["Date"])
                    tax_record += TaxRecord(
                        trade_revenue=row["SalePrice"] * exc_rate,
                        trade_cost=sold_row["PurchasePrice"] * sold_exc_rate,
                    )
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Lapse":
                pass
            elif row["Action"] == "Dividend":
                tax_record = TaxRecord(foreign_interest=row["Amount"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Tax Withholding":
                tax_record = TaxRecord(foreign_interest_withholding_tax=-row["Amount"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Wire Transfer":
                tax_record = TaxRecord(trade_cost=-row["FeesAndCommissions"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            else:
                raise ValueError(f"Unknown action: {row['Action']}")
        return tax_report

    def _parse_amount_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Parse money-like string columns and infer row currency."""
        for col in [
            "Amount",
            "SalePrice",
            "PurchasePrice",
            "FeesAndCommissions",
            "FairMarketValuePrice",
            "VestFairMarketValue",
        ]:
            parsed = df[col].str.extract(r"(-?)([$\u20AC£]?)([\d,\.]+)")
            sign = parsed[0].apply(lambda x: -1 if x == "-" else 1)
            currency = parsed[1].replace({"$": "USD", "€": "EUR", "£": "GBP"})
            amount = (
                parsed[2]
                .apply(lambda x: x.replace(",", "") if isinstance(x, str) else 0)
                .astype(float)
            )
            df[col] = sign * amount
            if "Currency" not in df.columns:
                df["Currency"] = currency
            else:
                df["Currency"] = df["Currency"].combine_first(currency)
        return df

    def _load_report(self) -> pd.DataFrame:
        """Load, merge and normalize Schwab action rows from CSV inputs."""
        reports: list[pd.DataFrame] = []
        for csv_file in self.csv_files:
            report = pd.read_csv(csv_file)
            reports.append(report)
        reports = sorted(
            reports,
            key=lambda x: pd.to_datetime(x["Date"]).max(),
            reverse=True,
        )
        df = pd.concat(reports, ignore_index=True).astype(
            {"Shares": "Int64", "Quantity": "Int64", "GrantId": "Int64"}
        )
        df["Date"] = pd.to_datetime(df["Date"])
        curr = 0
        data = defaultdict(list)
        for i, row in df.iterrows():
            if pd.isna(row["Date"]):
                data[curr].append(row)
            else:
                if curr in data:
                    data[curr] = (
                        pd.DataFrame(data[curr]).dropna(axis=1, how="all").assign(action_id=curr)
                    )
                curr = i
        if curr in data:
            data[curr] = pd.DataFrame(data[curr]).dropna(axis=1, how="all").assign(action_id=curr)
        df_additional = (
            pd.concat(data.values())
            .dropna(axis=1, how="all")
            .set_index("action_id")
            .rename_axis(index=None)
        )
        df = df[df["Date"].notna()].dropna(axis=1, how="all").join(df_additional)
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        df = self._parse_amount_columns(df)
        return df[::-1]
