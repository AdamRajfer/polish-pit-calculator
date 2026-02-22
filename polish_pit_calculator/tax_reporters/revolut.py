"""Revolut interest CSV tax reporter implementation."""

import pandas as pd

from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.file import FileTaxReporter


@TaxReporterRegistry.register
class RevolutInterestTaxReporter(FileTaxReporter):
    """Build a tax report from Revolut interest statement exports."""

    @classmethod
    def name(cls) -> str:
        """Return reporter name shown in app choices."""
        return "Revolut Interest"

    @classmethod
    def extension(cls) -> str:
        """Return accepted input file extension."""
        return ".csv"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Generate yearly domestic-interest tax record values."""
        df = pd.read_csv(self.path)
        df = df[df["Description"].str.startswith("Gross interest")]
        df["Completed Date"] = pd.to_datetime(df["Completed Date"], dayfirst=True)
        df = df.sort_values(by="Completed Date", ignore_index=True)
        df["Year"] = df["Completed Date"].dt.year
        df["Money in"] = (
            df["Money in"]
            .str.replace(",", "", regex=False)
            .str.extract(r"([+-]?\d+(?:\.\d*)?)")
            .astype(float)
        )
        tax_report = TaxReport()
        for year, df_year in df.groupby("Year"):
            tax_report[year] = TaxRecord(domestic_interest=df_year["Money in"].sum())
        return tax_report
