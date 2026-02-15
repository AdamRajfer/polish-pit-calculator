"""Coinbase CSV tax reporter implementation."""

from io import BytesIO

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import get_exchange_rate


class CoinbaseTaxReporter(TaxReporter):
    """Build a tax report from one or more Coinbase exports."""

    def __init__(self, *csv_files: BytesIO) -> None:
        """Store Coinbase CSV byte buffers."""
        self.csv_files = csv_files

    def generate(self) -> TaxReport:
        """Generate yearly crypto revenue and cost summary."""
        df = self._load_report()
        tax_report = TaxReport()
        for year, df_year in df.groupby("Year"):
            tax_report[year] = TaxRecord(
                crypto_revenue=df_year["Income"].sum(),
                crypto_cost=df_year["Cost"].sum(),
            )
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        """Load, normalize and convert Coinbase CSV rows to PLN values."""
        reports = []
        for csv_file in self.csv_files:
            report = pd.read_csv(csv_file, skiprows=3, parse_dates=["Timestamp"])
            reports.append(report)
        df = pd.concat(reports, ignore_index=True)
        df["Timestamp"] = df["Timestamp"].dt.date
        df["Year"] = df["Timestamp"].apply(lambda x: x.year)
        df = df[df["Transaction Type"].isin(["Advanced Trade Buy", "Advanced Trade Sell"])]
        for col in ["Subtotal", "Fees and/or Spread"]:
            df[col] = df[col].str.extract(r"[^\d](.*)").astype(float)
        df[["Cost", "Income"]] = 0.0
        buy = df[df["Transaction Type"] == "Advanced Trade Buy"]
        if not buy.empty:
            buy["Cost"] += buy["Subtotal"]
            buy["Cost"] += buy["Fees and/or Spread"]
        sell = df[df["Transaction Type"] == "Advanced Trade Sell"]
        if not sell.empty:
            sell["Income"] += sell["Subtotal"]
            sell["Cost"] += sell["Fees and/or Spread"]
        df = pd.concat([buy, sell])
        exc_rate = df.apply(
            lambda x: get_exchange_rate(
                currency=x["Price Currency"],
                date_=x["Timestamp"],
            ),
            axis=1,
        )
        df["Cost"] *= exc_rate
        df["Income"] *= exc_rate
        return df
