"""Tests for Charles Schwab employee-sponsored reporter behavior."""

import io
from datetime import date
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.config import TaxRecord
from src.schwab import SchwabEmployeeSponsoredTaxReporter


def _buf(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


class TestSchwabEmployeeSponsoredTaxReporter(TestCase):
    """Test Schwab parsing, loading and yearly aggregation logic."""

    def test_parse_amount_columns_parses_sign_amount_and_currency(self) -> None:
        """Test money parsing sets signed floats and inferred currencies."""
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(""))
        df = pd.DataFrame(
            [
                {
                    "Amount": "-$1,000.50",
                    "SalePrice": "$10.00",
                    "PurchasePrice": "$5.00",
                    "FeesAndCommissions": "$1.00",
                    "FairMarketValuePrice": "$2.00",
                    "VestFairMarketValue": "$3.00",
                },
                {
                    "Amount": "€2.00",
                    "SalePrice": "€7.00",
                    "PurchasePrice": "€4.00",
                    "FeesAndCommissions": "€0.50",
                    "FairMarketValuePrice": "€1.00",
                    "VestFairMarketValue": "€1.50",
                },
            ]
        )

        actual = getattr(reporter, "_parse_amount_columns")(df).reset_index(drop=True)
        expected = pd.DataFrame(
            [
                {
                    "Amount": -1000.5,
                    "SalePrice": 10.0,
                    "PurchasePrice": 5.0,
                    "FeesAndCommissions": 1.0,
                    "FairMarketValuePrice": 2.0,
                    "VestFairMarketValue": 3.0,
                    "Currency": "USD",
                },
                {
                    "Amount": 2.0,
                    "SalePrice": 7.0,
                    "PurchasePrice": 4.0,
                    "FeesAndCommissions": 0.5,
                    "FairMarketValuePrice": 1.0,
                    "VestFairMarketValue": 1.5,
                    "Currency": "EUR",
                },
            ]
        )
        assert_frame_equal(actual, expected, check_dtype=False)

    @patch("src.schwab.get_exchange_rate", return_value=2.0)
    def test_generate_handles_all_supported_actions(
        self,
        _rate: object,
    ) -> None:
        """Test yearly aggregation for deposit, sale, income and fee actions."""
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(""))
        df = pd.DataFrame(
            [
                {
                    "Date": date(2025, 1, 1),
                    "Currency": "USD",
                    "Action": "Deposit",
                    "Quantity": 2,
                    "Description": "PLAN-A",
                    "PurchasePrice": 5.0,
                },
                {
                    "Date": date(2025, 1, 2),
                    "Currency": "USD",
                    "Action": "Sale",
                    "Shares": 1,
                    "Type": "PLAN-A",
                    "FeesAndCommissions": 1.0,
                    "SalePrice": 10.0,
                },
                {
                    "Date": date(2025, 1, 3),
                    "Currency": "USD",
                    "Action": "Lapse",
                },
                {
                    "Date": date(2025, 1, 4),
                    "Currency": "USD",
                    "Action": "Dividend",
                    "Amount": 3.0,
                },
                {
                    "Date": date(2025, 1, 5),
                    "Currency": "USD",
                    "Action": "Tax Withholding",
                    "Amount": -0.5,
                },
                {
                    "Date": date(2025, 1, 6),
                    "Currency": "USD",
                    "Action": "Wire Transfer",
                    "FeesAndCommissions": -2.0,
                },
            ]
        )
        with patch.object(reporter, "_load_report", return_value=df):
            actual = reporter.generate().year_to_tax_record

        expected = {
            2025: TaxRecord(
                trade_revenue=20.0,
                trade_cost=16.0,
                foreign_interest=6.0,
                foreign_interest_withholding_tax=1.0,
            )
        }
        self.assertDictEqual(actual, expected)

    def test_generate_raises_for_unknown_action(self) -> None:
        """Test unsupported action names raise clear error."""
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(""))
        df = pd.DataFrame(
            [
                {
                    "Date": date(2025, 1, 1),
                    "Currency": "USD",
                    "Action": "UNSUPPORTED",
                }
            ]
        )
        with patch.object(reporter, "_load_report", return_value=df):
            with patch("src.schwab.get_exchange_rate", return_value=1.0):
                with self.assertRaisesRegex(ValueError, "Unknown action"):
                    reporter.generate()

    def test_load_report_sorts_merges_and_returns_date_rows(self) -> None:
        """Test report loading merges continuation rows and returns full dataframe."""
        rows = [
            {
                "Date": "2025-01-03",
                "Shares": 1,
                "Quantity": 1,
                "GrantId": 1,
                "Action": "Sale",
                "Description": "PLAN-A",
                "Type": "PLAN-A",
                "Amount": "$1.00",
                "SalePrice": "$10.00",
                "PurchasePrice": "$5.00",
                "FeesAndCommissions": "$1.00",
                "FairMarketValuePrice": "$0.00",
                "VestFairMarketValue": "$0.00",
                "Note": None,
            },
            {
                "Date": None,
                "Shares": None,
                "Quantity": None,
                "GrantId": None,
                "Action": None,
                "Description": None,
                "Type": None,
                "Amount": None,
                "SalePrice": None,
                "PurchasePrice": None,
                "FeesAndCommissions": None,
                "FairMarketValuePrice": None,
                "VestFairMarketValue": None,
                "Note": "continuation",
            },
        ]
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(pd.DataFrame(rows).to_csv(index=False)))

        with patch.object(reporter, "_parse_amount_columns", side_effect=lambda x: x) as parse:
            actual = getattr(reporter, "_load_report")().reset_index(drop=True)

        parse.assert_called_once()
        expected = pd.DataFrame(
            [
                {
                    "Date": date(2025, 1, 3),
                    "Shares": 1,
                    "Quantity": 1,
                    "GrantId": 1,
                    "Action": "Sale",
                    "Description": "PLAN-A",
                    "Type": "PLAN-A",
                    "Amount": "$1.00",
                    "SalePrice": "$10.00",
                    "PurchasePrice": "$5.00",
                    "FeesAndCommissions": "$1.00",
                    "FairMarketValuePrice": "$0.00",
                    "VestFairMarketValue": "$0.00",
                    "Note": "continuation",
                }
            ]
        )
        assert_frame_equal(actual, expected, check_dtype=False)

    def test_load_report_raises_when_no_continuation_rows_exist(self) -> None:
        """Test load report raises when additional-row block is empty."""
        rows = [
            {
                "Date": "2025-01-03",
                "Shares": 1,
                "Quantity": 1,
                "GrantId": 1,
                "Action": "Sale",
                "Description": "PLAN-A",
                "Type": "PLAN-A",
                "Amount": "$1.00",
                "SalePrice": "$10.00",
                "PurchasePrice": "$5.00",
                "FeesAndCommissions": "$1.00",
                "FairMarketValuePrice": "$0.00",
                "VestFairMarketValue": "$0.00",
            }
        ]
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(pd.DataFrame(rows).to_csv(index=False)))

        with self.assertRaisesRegex(ValueError, "No objects to concatenate"):
            getattr(reporter, "_load_report")()

    def test_load_report_handles_mid_stream_continuation_flush(self) -> None:
        """Test branch that flushes continuation rows when next action appears."""
        rows = [
            {
                "Date": "2025-01-03",
                "Shares": 1,
                "Quantity": 1,
                "GrantId": 1,
                "Action": "Sale",
                "Description": "PLAN-A",
                "Type": "PLAN-A",
                "Amount": "$1.00",
                "SalePrice": "$10.00",
                "PurchasePrice": "$5.00",
                "FeesAndCommissions": "$1.00",
                "FairMarketValuePrice": "$0.00",
                "VestFairMarketValue": "$0.00",
                "Note": None,
            },
            {
                "Date": None,
                "Shares": None,
                "Quantity": None,
                "GrantId": None,
                "Action": None,
                "Description": None,
                "Type": None,
                "Amount": None,
                "SalePrice": None,
                "PurchasePrice": None,
                "FeesAndCommissions": None,
                "FairMarketValuePrice": None,
                "VestFairMarketValue": None,
                "Note": "continuation-mid",
            },
            {
                "Date": "2025-01-02",
                "Shares": 2,
                "Quantity": 2,
                "GrantId": 2,
                "Action": "Deposit",
                "Description": "PLAN-B",
                "Type": "PLAN-B",
                "Amount": "$0.00",
                "SalePrice": "$0.00",
                "PurchasePrice": "$4.00",
                "FeesAndCommissions": "$0.00",
                "FairMarketValuePrice": "$0.00",
                "VestFairMarketValue": "$0.00",
                "Note": None,
            },
        ]
        reporter = SchwabEmployeeSponsoredTaxReporter(_buf(pd.DataFrame(rows).to_csv(index=False)))

        with patch.object(reporter, "_parse_amount_columns", side_effect=lambda x: x):
            actual = getattr(reporter, "_load_report")().reset_index(drop=True)

        expected = pd.DataFrame(
            [
                {
                    "Date": date(2025, 1, 2),
                    "Shares": 2,
                    "Quantity": 2,
                    "GrantId": 2,
                    "Action": "Deposit",
                    "Description": "PLAN-B",
                    "Type": "PLAN-B",
                    "Amount": "$0.00",
                    "SalePrice": "$0.00",
                    "PurchasePrice": "$4.00",
                    "FeesAndCommissions": "$0.00",
                    "FairMarketValuePrice": "$0.00",
                    "VestFairMarketValue": "$0.00",
                    "Note": float("nan"),
                },
                {
                    "Date": date(2025, 1, 3),
                    "Shares": 1,
                    "Quantity": 1,
                    "GrantId": 1,
                    "Action": "Sale",
                    "Description": "PLAN-A",
                    "Type": "PLAN-A",
                    "Amount": "$1.00",
                    "SalePrice": "$10.00",
                    "PurchasePrice": "$5.00",
                    "FeesAndCommissions": "$1.00",
                    "FairMarketValuePrice": "$0.00",
                    "VestFairMarketValue": "$0.00",
                    "Note": "continuation-mid",
                },
            ]
        )
        assert_frame_equal(actual, expected, check_dtype=False)
