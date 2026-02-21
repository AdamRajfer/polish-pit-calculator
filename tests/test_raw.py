"""Tests for raw CSV reporter behavior."""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.config import TaxRecord, TaxReport
from src.raw import RawTaxReporter


def _buf(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as file:
        file.write(text)
        return Path(file.name)


class TestRawTaxReporter(TestCase):
    """Test raw reporter loading and yearly aggregation."""

    def test_load_report_concatenates_and_fills_missing_values(self) -> None:
        """Test concatenation and fill of missing numeric values."""
        self.assertTrue(RawTaxReporter.validate_file_path(Path("x.csv")))
        self.assertEqual(
            RawTaxReporter.validate_file_path(Path("x.json")),
            "Only .csv files are supported.",
        )
        csv_1 = "year,description,trade_revenue,trade_cost\n2024,first,100.0,\n"
        csv_2 = "year,description,trade_revenue,trade_cost\n2025,second,50.0,30.0\n"
        reporter = RawTaxReporter(_buf(csv_1), _buf(csv_2))

        actual = getattr(reporter, "_load_report")().reset_index(drop=True)
        expected = pd.DataFrame(
            [
                {
                    "year": 2024,
                    "description": "first",
                    "trade_revenue": 100.0,
                    "trade_cost": 0.0,
                },
                {
                    "year": 2025,
                    "description": "second",
                    "trade_revenue": 50.0,
                    "trade_cost": 30.0,
                },
            ]
        )
        assert_frame_equal(actual, expected)

    def test_generate_groups_by_year_and_sums_tax_fields(self) -> None:
        """Test yearly grouping and summation for generated report."""
        csv_text = (
            "year,description,trade_revenue,trade_cost,domestic_interest\n"
            "2024,a,100.0,40.0,3.0\n"
            "2024,b,50.0,20.0,2.0\n"
            "2025,c,10.0,5.0,1.0\n"
        )
        reporter = RawTaxReporter(_buf(csv_text))

        report = reporter.generate()
        self.assertEqual(
            report,
            TaxReport(
                {
                    2024: TaxRecord(
                        trade_revenue=150.0,
                        trade_cost=60.0,
                        domestic_interest=5.0,
                    ),
                    2025: TaxRecord(
                        trade_revenue=10.0,
                        trade_cost=5.0,
                        domestic_interest=1.0,
                    ),
                }
            ),
        )

    def test_generate_uses_load_report_method(self) -> None:
        """Test generate path is isolated from CSV loading via _load_report mock."""
        reporter = RawTaxReporter(_buf("unused"))
        df = pd.DataFrame(
            [
                {
                    "year": 2025,
                    "description": "a",
                    "trade_revenue": 3.0,
                    "trade_cost": 1.0,
                },
                {
                    "year": 2025,
                    "description": "b",
                    "trade_revenue": 2.0,
                    "trade_cost": 2.0,
                },
            ]
        )
        with patch.object(reporter, "_load_report", return_value=df) as load:
            report = reporter.generate()
        load.assert_called_once_with()
        self.assertEqual(
            report,
            TaxReport(
                {
                    2025: TaxRecord(
                        trade_revenue=5.0,
                        trade_cost=3.0,
                    )
                }
            ),
        )
