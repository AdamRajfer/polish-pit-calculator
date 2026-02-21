"""Tests for Revolut interest reporter behavior."""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.config import TaxRecord
from src.revolut import RevolutInterestTaxReporter


def _buf(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as file:
        file.write(text)
        return Path(file.name)


class TestRevolutInterestTaxReporter(TestCase):
    """Test Revolut report loading and yearly aggregation."""

    def test_load_report_filters_interest_and_parses_amounts(self) -> None:
        """Test filtering only interest rows and parsing numeric amounts."""
        self.assertTrue(RevolutInterestTaxReporter.validate_file_path(Path("x.csv")))
        csv_text = (
            "Description,Completed Date,Money in\n"
            "Card payment,03-01-2025,10.00\n"
            'Gross interest paid,01-01-2025,"+1,234.50 PLN"\n'
            "Gross interest daily,02-01-2025,+10.00 PLN\n"
        )
        reporter = RevolutInterestTaxReporter(_buf(csv_text))

        actual = getattr(reporter, "_load_report")().reset_index(drop=True)
        expected = pd.DataFrame(
            [
                {
                    "Description": "Gross interest paid",
                    "Completed Date": pd.Timestamp("2025-01-01"),
                    "Money in": 1234.5,
                    "Year": 2025,
                },
                {
                    "Description": "Gross interest daily",
                    "Completed Date": pd.Timestamp("2025-01-02"),
                    "Money in": 10.0,
                    "Year": 2025,
                },
            ]
        )
        assert_frame_equal(actual, expected, check_dtype=False)

    def test_generate_sums_domestic_interest_by_year(self) -> None:
        """Test yearly domestic-interest totals in generated report."""
        csv_2024 = "Description,Completed Date,Money in\nGross interest paid,31-12-2024,+5.00 PLN\n"
        csv_2025 = (
            "Description,Completed Date,Money in\n"
            "Gross interest paid,01-01-2025,+3.00 PLN\n"
            "Gross interest paid,02-01-2025,+2.00 PLN\n"
        )
        reporter = RevolutInterestTaxReporter(_buf(csv_2024), _buf(csv_2025))

        report = reporter.generate()
        self.assertEqual(
            report.year_to_tax_record,
            {
                2024: TaxRecord(domestic_interest=5.0),
                2025: TaxRecord(domestic_interest=5.0),
            },
        )

    def test_generate_uses_load_report_method(self) -> None:
        """Test generate logic can be isolated by mocking _load_report."""
        reporter = RevolutInterestTaxReporter(_buf("unused"))
        df = pd.DataFrame(
            [
                {"Year": 2025, "Money in": 1.5},
                {"Year": 2025, "Money in": 2.5},
                {"Year": 2026, "Money in": 5.0},
            ]
        )
        with patch.object(reporter, "_load_report", return_value=df) as load:
            report = reporter.generate()
        load.assert_called_once_with()
        self.assertEqual(
            report.year_to_tax_record,
            {
                2025: TaxRecord(domestic_interest=4.0),
                2026: TaxRecord(domestic_interest=5.0),
            },
        )
