from collections import defaultdict

import pandas as pd

from polish_pit_calculator.config import TaxRecord, TaxReport, TaxReporter
from polish_pit_calculator.utils import fetch_exchange_rates, get_exchange_rate


class SchwabEmployeeSponsoredTaxReporter(TaxReporter):
    def generate(self) -> TaxReport:
        df = self._load_report()
        min_year = df["Date"].apply(lambda x: x.year).min()
        exc_rates = fetch_exchange_rates(min_year)
        remaining: dict[str, list[pd.Series]] = defaultdict(list)
        tax_report = TaxReport()
        for _, row in df.iterrows():
            year = row["Date"].year
            exc_rate = get_exchange_rate(
                row["Currency"], row["Date"], exc_rates
            )
            if row["Action"] == "Deposit":
                for _ in range(int(row["Quantity"])):
                    remaining[row["Description"]].append(row)
            elif row["Action"] == "Sale":
                tax_record = TaxRecord(
                    trade_cost=row["FeesAndCommissions"] * exc_rate
                )
                for _ in range(int(row["Shares"])):
                    sold_row = remaining[row["Type"]].pop(0)
                    sold_exc_rate = get_exchange_rate(
                        sold_row["Currency"], sold_row["Date"], exc_rates
                    )
                    tax_record += TaxRecord(
                        trade_revenue=row["SalePrice"] * exc_rate,
                        trade_cost=sold_row["PurchasePrice"] * sold_exc_rate,
                    )
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Lapse":
                pass
            elif row["Action"] == "Dividend":
                tax_record = TaxRecord(
                    foreign_interest=row["Amount"] * exc_rate
                )
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Tax Withholding":
                tax_record = TaxRecord(
                    foreign_interest_withholding_tax=-row["Amount"] * exc_rate
                )
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Wire Transfer":
                tax_record = TaxRecord(
                    trade_cost=-row["FeesAndCommissions"] * exc_rate
                )
                tax_report += TaxReport({year: tax_record})
            else:
                raise ValueError(f"Unknown action: {row['Action']}")
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        reports: list[pd.DataFrame] = []
        for arg in self.args:
            report = pd.read_csv(arg)
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
        df_notnull = df[df["Date"].notna()].dropna(axis=1, how="all")
        curr = 0
        data = defaultdict(list)
        for i, row in df.iterrows():
            if pd.isna(row["Date"]):
                data[curr].append(row)
            else:
                if curr in data:
                    data[curr] = (
                        pd.DataFrame(data[curr])
                        .dropna(axis=1, how="all")
                        .assign(action_id=curr)
                    )
                curr = i
        if curr in data:
            data[curr] = (
                pd.DataFrame(data[curr])
                .dropna(axis=1, how="all")
                .assign(action_id=curr)
            )
        df_additional = (
            pd.concat(data.values())
            .dropna(axis=1, how="all")
            .set_index("action_id")
            .rename_axis(index=None)
        )
        df = df_notnull.join(df_additional)
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        for col in [
            "Amount",
            "SalePrice",
            "PurchasePrice",
            "FeesAndCommissions",
            "FairMarketValuePrice",
            "VestFairMarketValue",
        ]:
            series = df[col].str.extract(r"(-?)([$\u20AC£]?)([\d,\.]+)")
            sign = series[0].apply(lambda x: -1 if x == "-" else 1)
            currency = series[1].replace({"$": "USD", "€": "EUR", "£": "GBP"})
            amount = (
                series[2]
                .apply(
                    lambda x: x.replace(",", "") if isinstance(x, str) else 0
                )
                .astype(float)
            )
            df[col] = sign * amount
            if "Currency" not in df.columns:
                df["Currency"] = currency
            else:
                df["Currency"] = df["Currency"].combine_first(currency)
        return df[::-1]
