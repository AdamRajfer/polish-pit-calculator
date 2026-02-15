"""Raw CSV tax reporter implementation."""

from io import BytesIO

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import load_and_concat_csv_files


class RawTaxReporter(TaxReporter):
    """Build a tax report directly from user-provided normalized CSV files."""

    def __init__(self, *csv_files: BytesIO) -> None:
        """Store raw CSV byte buffers."""
        self.csv_files = csv_files

    def generate(self) -> TaxReport:
        """Aggregate records by year and map rows to TaxRecord fields."""
        df = self._load_report()
        tax_report = TaxReport()
        for year, tax_record_data in (
            df.drop(columns="description").groupby("year").sum().iterrows()
        ):
            tax_report[year] = TaxRecord(**tax_record_data)
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        """Load and concatenate all raw CSV inputs."""
        return load_and_concat_csv_files(self.csv_files).fillna(0.0)
