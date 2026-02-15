"""Tests for Coinbase CSV reporter behavior."""

import io
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.coinbase import CoinbaseTaxReporter
from src.config import TaxRecord


def _buf(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def _coinbase_csv(rows: list[str]) -> io.BytesIO:
    prefix = "skip-1\nskip-2\nskip-3\n"
    header = "Timestamp,Transaction Type,Subtotal,Fees and/or Spread," + "Price Currency\n"
    body = "".join(rows)
    return _buf(prefix + header + body)


class TestCoinbaseTaxReporter(TestCase):
    """Test Coinbase report loading and yearly aggregation."""

    @patch("src.coinbase.get_exchange_rate", return_value=4.0)
    def test_load_report_buy_and_sell(self, _rate: object) -> None:
        """Test buy and sell rows are converted and scaled to PLN."""
        csv_file = _coinbase_csv(
            [
                "2025-01-02T12:00:00Z,Advanced Trade Buy,$100.00,$1.50,USD\n",
                "2025-01-03T12:00:00Z,Advanced Trade Sell,$120.00,$2.00,USD\n",
                "2025-01-04T12:00:00Z,Card Spend,$10.00,$0.00,USD\n",
            ]
        )
        reporter = CoinbaseTaxReporter(csv_file)

        actual = getattr(reporter, "_load_report")().reset_index(drop=True)
        expected = pd.DataFrame(
            [
                {
                    "Timestamp": pd.Timestamp("2025-01-02").date(),
                    "Transaction Type": "Advanced Trade Buy",
                    "Subtotal": 100.0,
                    "Fees and/or Spread": 1.5,
                    "Price Currency": "USD",
                    "Year": 2025,
                    "Cost": 406.0,
                    "Income": 0.0,
                },
                {
                    "Timestamp": pd.Timestamp("2025-01-03").date(),
                    "Transaction Type": "Advanced Trade Sell",
                    "Subtotal": 120.0,
                    "Fees and/or Spread": 2.0,
                    "Price Currency": "USD",
                    "Year": 2025,
                    "Cost": 8.0,
                    "Income": 480.0,
                },
            ]
        )
        assert_frame_equal(actual, expected)

    @patch("src.coinbase.get_exchange_rate", return_value=2.0)
    def test_generate_groups_years(self, _rate: object) -> None:
        """Test yearly sums for generated crypto report."""
        csv_file = _coinbase_csv(
            [
                "2024-02-01T12:00:00Z,Advanced Trade Buy,$10.00,$1.00,USD\n",
                "2024-02-02T12:00:00Z,Advanced Trade Sell,$15.00,$1.00,USD\n",
                "2025-02-01T12:00:00Z,Advanced Trade Sell,$5.00,$0.50,USD\n",
            ]
        )
        reporter = CoinbaseTaxReporter(csv_file)

        report = reporter.generate()
        self.assertEqual(
            report.year_to_tax_record,
            {
                2024: TaxRecord(crypto_revenue=30.0, crypto_cost=24.0),
                2025: TaxRecord(crypto_revenue=10.0, crypto_cost=1.0),
            },
        )

    @patch("src.coinbase.get_exchange_rate", return_value=1.0)
    def test_load_report_handles_only_buy_or_only_sell(
        self,
        _rate: object,
    ) -> None:
        """Test branch behavior when one trade side is empty."""
        buy_only = _coinbase_csv(["2025-01-02T12:00:00Z,Advanced Trade Buy,$10.00,$1.00,USD\n"])
        sell_only = _coinbase_csv(["2025-01-02T12:00:00Z,Advanced Trade Sell,$10.00,$1.00,USD\n"])

        buy_df = getattr(CoinbaseTaxReporter(buy_only), "_load_report")().reset_index(drop=True)
        sell_df = getattr(CoinbaseTaxReporter(sell_only), "_load_report")().reset_index(drop=True)

        buy_expected = pd.DataFrame(
            [
                {
                    "Timestamp": pd.Timestamp("2025-01-02").date(),
                    "Transaction Type": "Advanced Trade Buy",
                    "Subtotal": 10.0,
                    "Fees and/or Spread": 1.0,
                    "Price Currency": "USD",
                    "Year": 2025,
                    "Cost": 11.0,
                    "Income": 0.0,
                }
            ]
        )
        sell_expected = pd.DataFrame(
            [
                {
                    "Timestamp": pd.Timestamp("2025-01-02").date(),
                    "Transaction Type": "Advanced Trade Sell",
                    "Subtotal": 10.0,
                    "Fees and/or Spread": 1.0,
                    "Price Currency": "USD",
                    "Year": 2025,
                    "Cost": 1.0,
                    "Income": 10.0,
                }
            ]
        )
        assert_frame_equal(buy_df, buy_expected)
        assert_frame_equal(sell_df, sell_expected)

    def test_generate_uses_load_report_method(self) -> None:
        """Test generate path with mocked dataframe dependency."""
        reporter = CoinbaseTaxReporter(_buf("unused"))
        df = pd.DataFrame(
            [
                {"Year": 2025, "Income": 7.0, "Cost": 3.0},
                {"Year": 2025, "Income": 1.0, "Cost": 2.0},
                {"Year": 2026, "Income": 2.0, "Cost": 5.0},
            ]
        )
        with patch.object(reporter, "_load_report", return_value=df) as load_mock:
            actual = reporter.generate().year_to_tax_record
        expected = {
            2025: TaxRecord(crypto_revenue=8.0, crypto_cost=5.0),
            2026: TaxRecord(crypto_revenue=2.0, crypto_cost=5.0),
        }
        self.assertEqual(load_mock.call_count, 1)
        self.assertDictEqual(actual, expected)
