"""Tests for Coinbase CSV reporter behavior."""

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from polish_pit_calculator.config import TaxRecord
from polish_pit_calculator.tax_reporters import CoinbaseTaxReporter


def _buf(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as file:
        file.write(text)
        return Path(file.name)


def _coinbase_csv(rows: list[str]) -> Path:
    prefix = "skip-1\nskip-2\nskip-3\n"
    header = "Timestamp,Transaction Type,Subtotal,Fees and/or Spread,Price Currency\n"
    body = "".join(rows)
    return _buf(prefix + header + body)


class TestCoinbaseTaxReporter(TestCase):
    """Test Coinbase yearly aggregation behavior."""

    def test_metadata(self) -> None:
        """Reporter metadata should expose expected values."""
        self.assertEqual(CoinbaseTaxReporter.name(), "Coinbase")
        self.assertEqual(CoinbaseTaxReporter.extension(), ".csv")

    @patch(
        "polish_pit_calculator.tax_reporters.coinbase.ExchangeRatesCache.get_exchange_rate",
        return_value=2.0,
    )
    def test_generate_groups_years_and_converts_buy_sell_values(self, _rate: object) -> None:
        """generate should normalize buy/sell rows and aggregate yearly tax values."""
        csv_file = _coinbase_csv(
            [
                "2024-02-01T12:00:00Z,Advanced Trade Buy,$10.00,$1.00,USD\n",
                "2024-02-02T12:00:00Z,Advanced Trade Sell,$15.00,$1.00,USD\n",
                "2025-02-01T12:00:00Z,Advanced Trade Sell,$5.00,$0.50,USD\n",
                "2025-02-03T12:00:00Z,Card Spend,$100.00,$0.00,USD\n",
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

    @patch(
        "polish_pit_calculator.tax_reporters.coinbase.ExchangeRatesCache.get_exchange_rate",
        return_value=1.0,
    )
    def test_generate_handles_only_buy_or_only_sell(self, _rate: object) -> None:
        """generate should work when one side of trades is empty."""
        buy_only = _coinbase_csv(["2025-01-02T12:00:00Z,Advanced Trade Buy,$10.00,$1.00,USD\n"])
        sell_only = _coinbase_csv(["2025-01-02T12:00:00Z,Advanced Trade Sell,$10.00,$1.00,USD\n"])

        buy_report = CoinbaseTaxReporter(buy_only).generate()
        sell_report = CoinbaseTaxReporter(sell_only).generate()

        self.assertEqual(
            buy_report.year_to_tax_record,
            {2025: TaxRecord(crypto_revenue=0.0, crypto_cost=11.0)},
        )
        self.assertEqual(
            sell_report.year_to_tax_record,
            {2025: TaxRecord(crypto_revenue=10.0, crypto_cost=1.0)},
        )
