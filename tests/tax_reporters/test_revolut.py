"""Tests for Revolut interest reporter behavior."""

import tempfile
from pathlib import Path
from unittest import TestCase

from polish_pit_calculator.config import TaxRecord
from polish_pit_calculator.tax_reporters import RevolutInterestTaxReporter


def _buf(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as file:
        file.write(text)
        return Path(file.name)


class TestRevolutInterestTaxReporter(TestCase):
    """Test Revolut yearly aggregation behavior."""

    def test_metadata(self) -> None:
        """Reporter metadata should expose expected values."""
        self.assertEqual(RevolutInterestTaxReporter.name(), "Revolut Interest")
        self.assertEqual(RevolutInterestTaxReporter.extension(), ".csv")

    def test_generate_filters_interest_rows_parses_amounts_and_groups_years(self) -> None:
        """generate should keep interest rows only, parse amounts and aggregate by year."""
        csv_text = (
            "Description,Completed Date,Money in\n"
            "Card payment,03-01-2025,10.00\n"
            'Gross interest paid,31-12-2024,"+1,234.50 PLN"\n'
            "Gross interest daily,01-01-2025,+3.00 PLN\n"
            "Gross interest paid,02-01-2025,+2.00 PLN\n"
        )
        reporter = RevolutInterestTaxReporter(_buf(csv_text))

        report = reporter.generate()
        self.assertEqual(
            report.year_to_tax_record,
            {
                2024: TaxRecord(domestic_interest=1234.5),
                2025: TaxRecord(domestic_interest=5.0),
            },
        )
