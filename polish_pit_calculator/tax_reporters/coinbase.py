"""Coinbase CSV tax reporter implementation."""

import pandas as pd

from polish_pit_calculator.caches import ExchangeRatesCache
from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.file import FileTaxReporter


@TaxReporterRegistry.register
class CoinbaseTaxReporter(FileTaxReporter):
    """Build a tax report from one or more Coinbase exports."""

    @classmethod
    def name(cls):
        return "Coinbase"

    @classmethod
    def extension(cls) -> str:
        """Return accepted input file extension."""
        return ".csv"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Generate yearly crypto revenue and cost summary."""
        df = pd.read_csv(self.path, skiprows=3, parse_dates=["Timestamp"])
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
            lambda x: ExchangeRatesCache.get_exchange_rate(
                currency=x["Price Currency"],
                date_=x["Timestamp"],
            ),
            axis=1,
        )
        df["Cost"] *= exc_rate
        df["Income"] *= exc_rate
        tax_report = TaxReport()
        for year, df_year in df.groupby("Year"):
            tax_report[year] = TaxRecord(
                crypto_revenue=df_year["Income"].sum(),
                crypto_cost=df_year["Cost"].sum(),
            )
        return tax_report
