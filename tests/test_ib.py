"""Tests for Interactive Brokers Trade Cash reporter behavior."""

from datetime import datetime
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.config import TaxRecord
from src.ib import IBTradeCashTaxReporter


def _buf(text: str) -> BytesIO:
    return BytesIO(text.encode("utf-8"))


class TestIBTradeCashTaxReporter(TestCase):
    """Test IB Trade Cash loading, matching and yearly aggregation."""

    def test_load_report_filters_prefix_and_cleans_description(self) -> None:
        """Test prefixed CSV parsing, date filtering and regex cleanup."""
        trades_csv = (
            "Trades,Header,Date/Time,Currency,Symbol,Quantity,Proceeds,Comm/Fee,Description\n"
            "Trades,Data,2025-01-02 10:00:00,USD,AAPL,1,-100,-1,ACME (US)\n"
            "Trades,Data,,USD,AAPL,1,-100,-1,DROP-ME\n"
        )
        other_csv = (
            "Dividends,Header,Date,Currency,Description,Amount\n"
            "Dividends,Data,2025-01-05,USD,ACME - paid,10\n"
        )
        reporter = IBTradeCashTaxReporter(_buf(trades_csv), _buf(other_csv))

        actual = getattr(reporter, "_load_report")(
            "Trades",
            "Date/Time",
            r"\s*\([^()]*\)\s*$",
        )
        expected = pd.DataFrame(
            [
                {
                    "Trades": "Trades",
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-02 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "AAPL",
                    "Quantity": 1,
                    "Proceeds": -100,
                    "Comm/Fee": -1,
                    "Description": "ACME",
                    "Year": 2025,
                }
            ]
        )
        assert_frame_equal(actual.reset_index(drop=True), expected)

    def test_load_report_without_regex_keeps_description(self) -> None:
        """Test load report skips description replacement when regex is None."""
        trades_csv = (
            "Trades,Header,Date/Time,Currency,Symbol,Quantity,Proceeds,Comm/Fee,Description\n"
            "Trades,Data,2025-01-02 10:00:00,USD,AAPL,1,-100,-1,ACME (US)\n"
        )
        reporter = IBTradeCashTaxReporter(_buf(trades_csv))

        actual = getattr(reporter, "_load_report")(
            "Trades",
            "Date/Time",
            None,
        )
        expected = pd.DataFrame(
            [
                {
                    "Trades": "Trades",
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-02 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "AAPL",
                    "Quantity": 1,
                    "Proceeds": -100,
                    "Comm/Fee": -1,
                    "Description": "ACME (US)",
                    "Year": 2025,
                }
            ]
        )
        assert_frame_equal(actual.reset_index(drop=True), expected)

    @patch("src.ib.get_exchange_rate", return_value=1.0)
    def test_load_trades_fifo_covers_all_quantity_branches(
        self,
        _rate: object,
    ) -> None:
        """Test FIFO matching for equal, lower and higher buy quantities."""
        reporter = IBTradeCashTaxReporter(_buf(""))
        trades_df = pd.DataFrame(
            [
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-02 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "EQ",
                    "Quantity": 5,
                    "Proceeds": -500,
                    "Comm/Fee": -5,
                    "Year": 2025,
                },
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-03 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "EQ",
                    "Quantity": -5,
                    "Proceeds": 600,
                    "Comm/Fee": -6,
                    "Year": 2025,
                },
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-04 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "LT",
                    "Quantity": 2,
                    "Proceeds": -200,
                    "Comm/Fee": -2,
                    "Year": 2025,
                },
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-05 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "LT",
                    "Quantity": -3,
                    "Proceeds": 300,
                    "Comm/Fee": -3,
                    "Year": 2025,
                },
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-06 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "GT",
                    "Quantity": 5,
                    "Proceeds": -500,
                    "Comm/Fee": -5,
                    "Year": 2025,
                },
                {
                    "Header": "Data",
                    "Date/Time": pd.Timestamp("2025-01-07 10:00:00"),
                    "Currency": "USD",
                    "Symbol": "GT",
                    "Quantity": -2,
                    "Proceeds": 220,
                    "Comm/Fee": -2,
                    "Year": 2025,
                },
            ]
        )

        with patch.object(reporter, "_load_report", return_value=trades_df):
            actual = getattr(reporter, "_load_trades")()

        expected = pd.DataFrame(
            [
                {
                    "buy_price": 505.0,
                    "buy_price_pln": 505.0,
                    "sell_price": 594.0,
                    "sell_price_pln": 594.0,
                    "Year": 2025,
                },
                {
                    "buy_price": 202.0,
                    "buy_price_pln": 202.0,
                    "sell_price": 198.0,
                    "sell_price_pln": 198.0,
                    "Year": 2025,
                },
                {
                    "buy_price": 202.0,
                    "buy_price_pln": 202.0,
                    "sell_price": 218.0,
                    "sell_price_pln": 218.0,
                    "Year": 2025,
                },
            ]
        )
        actual_sorted = actual.sort_values(["buy_price", "sell_price"]).reset_index(drop=True)
        expected_sorted = expected.sort_values(["buy_price", "sell_price"]).reset_index(drop=True)
        assert_frame_equal(actual_sorted, expected_sorted)

    @patch("src.ib.get_exchange_rate", return_value=2.0)
    def test_load_dividends_or_interests_merges_withholding(
        self,
        _rate: object,
    ) -> None:
        """Test withholding merge, abs conversion and PLN scaling."""
        reporter = IBTradeCashTaxReporter(_buf(""))
        income = pd.DataFrame(
            [
                {
                    "Currency": "USD",
                    "Date": pd.Timestamp("2025-01-05"),
                    "Description": "ACME",
                    "Amount": 10.0,
                    "Year": 2025,
                },
                {
                    "Currency": "USD",
                    "Date": pd.Timestamp("2025-01-06"),
                    "Description": "NO W-TAX",
                    "Amount": 5.0,
                    "Year": 2025,
                },
            ]
        )
        wtax = pd.DataFrame(
            [
                {
                    "Currency": "USD",
                    "Date": pd.Timestamp("2025-01-05"),
                    "Description": "ACME",
                    "Amount": -1.0,
                    "Year": 2025,
                }
            ]
        )

        with patch.object(reporter, "_load_report", side_effect=[income, wtax]):
            actual = getattr(reporter, "_load_dividends_or_interests")(
                "Dividends",
                r"x",
                r"y",
            )
        expected = pd.DataFrame(
            [
                {
                    "Currency": "USD",
                    "Date": pd.Timestamp("2025-01-05").date(),
                    "Description": "ACME",
                    "Amount": 10.0,
                    "Date_wtax": pd.Timestamp("2025-01-05").date(),
                    "Amount_wtax": 1.0,
                    "Year": 2025.0,
                    "Amount_pln": 20.0,
                    "Amount_wtax_pln": 2.0,
                },
                {
                    "Currency": "USD",
                    "Date": pd.Timestamp("2025-01-06").date(),
                    "Description": "NO W-TAX",
                    "Amount": 5.0,
                    "Date_wtax": float("nan"),
                    "Amount_wtax": 0.0,
                    "Year": float("nan"),
                    "Amount_pln": 10.0,
                    "Amount_wtax_pln": 0.0,
                },
            ]
        )
        assert_frame_equal(
            actual.reset_index(drop=True),
            expected,
            check_dtype=False,
        )

    def test_sum_helpers_return_zeros_for_none_inputs(self) -> None:
        """Test helper methods return zero tuples when no data is provided."""
        reporter = IBTradeCashTaxReporter(_buf(""))

        trade_values = getattr(reporter, "_sum_trade_values")(None, 2025)
        interest_values = getattr(reporter, "_sum_interest_values")(None, 2025)

        self.assertEqual(trade_values, (0.0, 0.0))
        self.assertEqual(interest_values, (0.0, 0.0))

    def test_generate_aggregates_years_and_defaults_missing_year_data(self) -> None:
        """Test report generation includes range up to current year with zeros."""
        reporter = IBTradeCashTaxReporter(_buf(""))
        trades = pd.DataFrame([{"Year": 2024, "sell_price_pln": 100.0, "buy_price_pln": 80.0}])
        dividends = pd.DataFrame([{"Year": 2024, "Amount_pln": 10.0, "Amount_wtax_pln": 1.0}])
        interests = pd.DataFrame([{"Year": 2024, "Amount_pln": 5.0, "Amount_wtax_pln": 0.5}])

        with patch.object(reporter, "_load_trades", return_value=trades):
            with patch.object(
                reporter,
                "_load_dividends_or_interests",
                side_effect=[dividends, interests],
            ):
                with patch("src.ib.datetime") as dt_mock:
                    dt_mock.now.return_value = datetime(2025, 1, 1)
                    report = reporter.generate()

        self.assertEqual(
            report.year_to_tax_record,
            {
                2024: TaxRecord(
                    trade_revenue=100.0,
                    trade_cost=80.0,
                    foreign_interest=15.0,
                    foreign_interest_withholding_tax=1.5,
                ),
                2025: TaxRecord(
                    trade_revenue=0.0,
                    trade_cost=0.0,
                    foreign_interest=0.0,
                    foreign_interest_withholding_tax=0.0,
                ),
            },
        )
