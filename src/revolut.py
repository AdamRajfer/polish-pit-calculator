"""Revolut interest CSV tax reporter implementation."""

from io import BytesIO

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import load_and_concat_csv_files


class RevolutInterestTaxReporter(TaxReporter):
    """Build a tax report from Revolut interest statement exports."""

    def __init__(self, *csv_files: BytesIO) -> None:
        """Store Revolut CSV byte buffers."""
        self.csv_files = csv_files

    def generate(self) -> TaxReport:
        """Generate yearly domestic-interest tax record values."""
        df = self._load_report()
        tax_report = TaxReport()
        for year, df_year in df.groupby("Year"):
            tax_report[year] = TaxRecord(domestic_interest=df_year["Money in"].sum())
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        """Load and normalize Revolut interest rows."""
        df = load_and_concat_csv_files(self.csv_files)
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
        return df
