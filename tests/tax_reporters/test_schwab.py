"""Tests for Charles Schwab employee-sponsored reporter behavior."""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import cast
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from polish_pit_calculator.config import TaxRecord, TaxReportLogs
from polish_pit_calculator.tax_reporters import CharlesSchwabEmployeeSponsoredTaxReporter
from polish_pit_calculator.tax_reporters.schwab import _ScaleContext, _SplitParams


def _json_buf(payload: object) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as file:
        json.dump(payload, file)
        return Path(file.name)


class TestCharlesSchwabEmployeeSponsoredTaxReporter(TestCase):
    """Test Schwab parsing, loading and yearly aggregation logic."""

    def test_parse_amount_columns_parses_sign_amount_and_currency(self) -> None:
        """Test money parsing sets signed floats and inferred currencies."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        self.assertEqual(CharlesSchwabEmployeeSponsoredTaxReporter.extension(), ".json")
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

    def test_parse_amount_columns_handles_missing_columns(self) -> None:
        """Test missing money columns are created and parsed as zero."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        df = pd.DataFrame([{"Amount": "$2.00"}])
        actual = getattr(reporter, "_parse_amount_columns")(df)
        required_columns = {"Amount", "SalePrice", "PurchasePrice", "Currency"}
        required_columns |= {"FeesAndCommissions", "FairMarketValuePrice", "VestFairMarketValue"}
        assert set(actual.columns) >= required_columns
        assert actual.iloc[0]["Amount"] == 2.0
        assert actual.iloc[0]["FeesAndCommissions"] == 0.0
        assert actual.iloc[0]["Currency"] == "USD"

    def test_parse_amount_columns_keeps_existing_currency(self) -> None:
        """Test prefilled row currency is not overwritten by parsed values."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        df = pd.DataFrame(
            [
                {
                    "Currency": "GBP",
                    "Amount": "$2.00",
                    "SalePrice": "$3.00",
                    "PurchasePrice": "$1.00",
                    "FeesAndCommissions": "$0.01",
                    "FairMarketValuePrice": "$4.00",
                    "VestFairMarketValue": "$5.00",
                }
            ]
        )
        actual = getattr(reporter, "_parse_amount_columns")(df)
        assert actual.iloc[0]["Currency"] == "GBP"

    @patch(
        "polish_pit_calculator.tax_reporters.schwab.ExchangeRatesCache.get_exchange_rate",
        return_value=2.0,
    )
    def test_generate_handles_all_supported_actions(
        self,
        _rate: object,
    ) -> None:
        """Test yearly aggregation for deposit, sale, income and fee actions."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
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
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
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
            with patch(
                "polish_pit_calculator.tax_reporters.schwab.ExchangeRatesCache.get_exchange_rate",
                return_value=1.0,
            ):
                with self.assertRaisesRegex(ValueError, "Unknown action"):
                    reporter.generate()

    def test_flatten_transaction_handles_missing_details_and_type_fallback(self) -> None:
        """Test flattening creates one row and defaults type to description."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        transaction: dict[str, object] = {
            "Date": "01/01/2025",
            "Action": "Sale",
            "Description": "Share Sale",
            "Quantity": "10",
            "FeesAndCommissions": "$1.00",
            "Amount": "$10.00",
            "TransactionDetails": None,
        }

        rows = getattr(reporter, "_flatten_transaction")(transaction)
        assert rows == [
            {
                "Date": "01/01/2025",
                "Action": "Sale",
                "Description": "Share Sale",
                "Quantity": "10",
                "Amount": "$10.00",
                "FeesAndCommissions": "$1.00",
                "Type": "Share Sale",
                "Shares": None,
                "SalePrice": None,
                "PurchasePrice": None,
                "FairMarketValuePrice": None,
                "VestFairMarketValue": None,
            }
        ]

    def test_flatten_transaction_ignores_invalid_detail_items(self) -> None:
        """Test malformed detail rows are ignored and fallback row is created."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        transaction: dict[str, object] = {
            "Date": "01/01/2025",
            "Action": "Sale",
            "Description": "Share Sale",
            "Quantity": "10",
            "FeesAndCommissions": "$1.00",
            "Amount": "$10.00",
            "TransactionDetails": [{"Details": "invalid"}, "invalid"],
        }

        rows = getattr(reporter, "_flatten_transaction")(transaction)
        assert len(rows) == 1
        assert rows[0]["Type"] == "Share Sale"
        assert rows[0]["Shares"] is None

    def test_flatten_transaction_keeps_rs_deposit_purchase_empty_and_splits_sale_fee(self) -> None:
        """Test RS deposit keeps zero basis and multi-lot sale fee assignment."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        deposit: dict[str, object] = {
            "Date": "01/01/2025",
            "Action": "Deposit",
            "Description": "RS",
            "Quantity": "3",
            "FeesAndCommissions": None,
            "Amount": None,
            "TransactionDetails": [
                {"Details": {"VestFairMarketValue": "$5.00", "Type": "RS"}},
            ],
        }
        sale: dict[str, object] = {
            "Date": "01/02/2025",
            "Action": "Sale",
            "Description": "Share Sale",
            "Quantity": "3",
            "FeesAndCommissions": "$0.05",
            "Amount": "$35.95",
            "TransactionDetails": [
                {"Details": {"Type": "RS", "Shares": "1", "SalePrice": "$10.00"}},
                {"Details": {"Type": "RS", "Shares": "2", "SalePrice": "$13.00"}},
            ],
        }

        deposit_rows = getattr(reporter, "_flatten_transaction")(deposit)
        sale_rows = getattr(reporter, "_flatten_transaction")(sale)

        assert deposit_rows[0]["PurchasePrice"] is None
        assert sale_rows[0]["FeesAndCommissions"] == "$0.05"
        assert sale_rows[1]["FeesAndCommissions"] == "$0.00"

    def test_load_report_parses_json_sorts_and_normalizes_rows(self) -> None:
        """Test JSON loading flattens rows, parses values and sorts chronologically."""
        payload = {
            "Transactions": [
                {
                    "Date": "01/02/2025",
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": "3",
                    "FeesAndCommissions": "$0.05",
                    "Amount": "$35.95",
                    "TransactionDetails": [
                        {"Details": {"Type": "RS", "Shares": "1", "SalePrice": "$10.00"}},
                        {"Details": {"Type": "RS", "Shares": "2", "SalePrice": "$13.00"}},
                    ],
                },
                {
                    "Date": "01/01/2025",
                    "Action": "Deposit",
                    "Description": "RS",
                    "Quantity": "3",
                    "FeesAndCommissions": None,
                    "Amount": None,
                    "TransactionDetails": [
                        {"Details": {"VestFairMarketValue": "$5.00", "Type": "RS"}},
                    ],
                },
            ]
        }
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf(payload))
        actual = getattr(reporter, "_load_report")([]).reset_index(drop=True)

        expected = pd.DataFrame(
            [
                {
                    "Date": date(2025, 1, 1),
                    "Action": "Deposit",
                    "Description": "RS",
                    "Quantity": 3,
                    "Amount": 0.0,
                    "FeesAndCommissions": 0.0,
                    "Type": "RS",
                    "Shares": 0,
                    "SalePrice": 0.0,
                    "PurchasePrice": 0.0,
                    "FairMarketValuePrice": 0.0,
                    "VestFairMarketValue": 5.0,
                    "Currency": "USD",
                },
                {
                    "Date": date(2025, 1, 2),
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": 3,
                    "Amount": 35.95,
                    "FeesAndCommissions": 0.05,
                    "Type": "RS",
                    "Shares": 1,
                    "SalePrice": 10.0,
                    "PurchasePrice": 0.0,
                    "FairMarketValuePrice": 0.0,
                    "VestFairMarketValue": 0.0,
                    "Currency": "USD",
                },
                {
                    "Date": date(2025, 1, 2),
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": 3,
                    "Amount": 35.95,
                    "FeesAndCommissions": 0.0,
                    "Type": "RS",
                    "Shares": 2,
                    "SalePrice": 13.0,
                    "PurchasePrice": 0.0,
                    "FairMarketValuePrice": 0.0,
                    "VestFairMarketValue": 0.0,
                    "Currency": "USD",
                },
            ]
        )
        assert_frame_equal(actual, expected, check_dtype=False)

    def test_load_report_ignores_non_object_payloads(self) -> None:
        """Test loader skips malformed payloads and non-dict transaction rows."""
        payload_valid = {
            "Transactions": [
                {
                    "Date": "01/01/2025",
                    "Action": "Dividend",
                    "Description": "Credit",
                    "Quantity": None,
                    "FeesAndCommissions": None,
                    "Amount": "$1.50",
                    "TransactionDetails": [],
                },
                "invalid-transaction",
            ]
        }
        reporter_non_dict = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf(["not-a-dict"]))
        assert getattr(reporter_non_dict, "_load_report")([]).empty

        reporter_bad_shape = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({"bad": "shape"}))
        assert getattr(reporter_bad_shape, "_load_report")([]).empty

        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf(payload_valid))

        actual = getattr(reporter, "_load_report")([]).reset_index(drop=True)
        assert len(actual.index) == 1
        assert (actual.iloc[0]["Action"], actual.iloc[0]["Amount"]) == ("Dividend", 1.5)
        with patch.object(
            reporter, "_align_and_validate_payload", return_value={"Transactions": "bad"}
        ):
            assert getattr(reporter, "_load_report")([]).empty

    def test_load_report_returns_empty_for_empty_transactions(self) -> None:
        """Test empty JSON transaction list yields empty dataframe."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({"Transactions": []}))
        actual = getattr(reporter, "_load_report")([])
        assert actual.empty

    def test_load_report_runs_alignment_before_flatten(self) -> None:
        """Test pre-split rows are aligned in-memory before parsing."""
        payload = {
            "Transactions": [
                {
                    "Date": "01/19/2024",
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": "1",
                    "FeesAndCommissions": "$0.00",
                    "Amount": "$580.00",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "Type": "RS",
                                "Shares": "1",
                                "SalePrice": "$580.00",
                                "VestFairMarketValue": "$422.39",
                                "TotalCostBasis": "$422.39",
                            }
                        }
                    ],
                },
                {
                    "Date": "01/06/2025",
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": "48",
                    "FeesAndCommissions": "$0.00",
                    "Amount": "$7,200.00",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "Type": "RS",
                                "Shares": "48",
                                "SalePrice": "$150.00",
                                "VestFairMarketValue": "$42.239",
                                "TotalCostBasis": "$2,027.47",
                            }
                        }
                    ],
                },
            ]
        }
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf(payload))

        logs = TaxReportLogs()
        actual = getattr(reporter, "_load_report")(logs).reset_index(drop=True)
        assert actual.iloc[0]["Quantity"] == 10
        assert actual.iloc[0]["SalePrice"] == 58.0
        log_lines = logs
        assert any("Shares:" in line for line in log_lines) and any(
            "Quantity" in line for line in log_lines
        )
        assert all(
            "\x1b[36m" in line and "\x1b[95m" in line and "\x1b[33m" in line for line in log_lines
        )

    def test_align_and_validate_payload_raises_on_validation_errors(self) -> None:
        """Test validation errors from aligner are surfaced to caller."""
        payload = {
            "Transactions": [
                {
                    "Date": "01/19/2024",
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": "1",
                    "FeesAndCommissions": "$0.00",
                    "Amount": "$700.00",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "Type": "RS",
                                "Shares": "1",
                                "SalePrice": "$580.00",
                                "VestFairMarketValue": "$422.39",
                                "TotalCostBasis": "$422.39",
                            }
                        }
                    ],
                },
                {
                    "Date": "01/06/2025",
                    "Action": "Sale",
                    "Description": "Share Sale",
                    "Quantity": "48",
                    "FeesAndCommissions": "$0.00",
                    "Amount": "$7,200.00",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "Type": "RS",
                                "Shares": "48",
                                "SalePrice": "$150.00",
                                "VestFairMarketValue": "$42.239",
                                "TotalCostBasis": "$2,027.47",
                            }
                        }
                    ],
                },
            ]
        }
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf(payload))

        with self.assertRaisesRegex(ValueError, "sale amount mismatch"):
            getattr(reporter, "_align_and_validate_payload")(payload, TaxReportLogs())


class TestSchwabAlignmentHelpers(TestCase):
    """Exercise private split-alignment helper paths with deterministic payloads."""

    def _context(self, *, default_scale_when_unknown: bool = True) -> _ScaleContext:
        return _ScaleContext(
            split=_SplitParams(split_date=date(2024, 6, 10), factor=10, is_reverse=False),
            references=({}, {}, {}, None, None),
            default_scale_when_unknown=default_scale_when_unknown,
        )

    def test_align_transaction_guard_paths(self) -> None:
        """Cover guarded exits in transaction and detail alignment helpers."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        align_tx = getattr(reporter, "_align_transaction_before_split")
        logs = TaxReportLogs()

        assert getattr(reporter, "_align_and_validate_payload")({"Transactions": "bad"}, logs) == {
            "Transactions": "bad"
        }
        assert align_tx("bad", self._context(), frozenset({"Sale"}), logs) is False
        assert align_tx({"Action": "Dividend"}, self._context(), frozenset({"Sale"}), logs) is False
        assert (
            align_tx(
                {"Action": "Sale", "Date": "06/10/2024", "TransactionDetails": []},
                self._context(),
                frozenset({"Sale"}),
                logs,
            )
            is False
        )
        assert (
            align_tx(
                {"Action": "Sale", "Date": "01/10/2024", "TransactionDetails": "bad"},
                self._context(),
                frozenset({"Sale"}),
                logs,
            )
            is False
        )
        assert (
            align_tx(
                {
                    "Action": "Sale",
                    "Date": "01/10/2024",
                    "Quantity": "1",
                    "TransactionDetails": [{"Details": {"Shares": ""}}],
                },
                self._context(default_scale_when_unknown=False),
                frozenset({"Sale"}),
                logs,
            )
            is False
        )
        logs = TaxReportLogs()
        empty_detail_tx = {
            "Action": "Sale",
            "Date": "01/10/2024",
            "Quantity": "0",
            "TransactionDetails": [{"Details": {}}],
        }
        assert align_tx(empty_detail_tx, self._context(), frozenset({"Sale"}), logs) is True
        assert not logs
        logs = TaxReportLogs()
        missing_type_tx = {
            "Action": "Sale",
            "Date": "01/10/2024",
            "Quantity": "0",
            "TransactionDetails": [{"Details": {"SalePrice": "$100.00"}}],
        }
        assert align_tx(missing_type_tx, self._context(), frozenset({"Sale"}), logs) is True
        assert any("SalePrice" in line for line in logs)

        logs = TaxReportLogs()
        no_qty_change_tx = {
            "Action": "Sale",
            "Date": "01/10/2024",
            "Quantity": "0",
            "TransactionDetails": [{"Details": {"Type": "RS", "SalePrice": "$100.00"}}],
        }
        assert align_tx(no_qty_change_tx, self._context(), frozenset({"Sale"}), logs) is True
        log_lines = logs
        assert any("SalePrice" in line for line in log_lines) and not any(
            "Quantity" in line for line in log_lines
        )
        logs = TaxReportLogs()
        deposit_qty_only_tx = {
            "Action": "Deposit",
            "Date": "01/10/2024",
            "Description": "RS",
            "Quantity": "2",
            "TransactionDetails": [{"Details": {"Type": "RS"}}],
        }
        assert (
            align_tx(
                deposit_qty_only_tx,
                self._context(),
                frozenset({"Deposit"}),
                logs,
            )
            is True
        )
        assert any(
            "Deposit RS" in line
            and "Quantity:" in line
            and "\x1b[31m2\x1b[0m" in line
            and "\x1b[32m20\x1b[0m" in line
            for line in logs
        )
        detail_rows = [1, {"Details": "bad"}, {"Details": {}}]
        assert list(getattr(reporter, "_iter_detail_dicts")(detail_rows)) == [{}]

    def test_align_and_validate_payload_scales_and_logs_pre_split_deposits(self) -> None:
        """Pre-split deposits should be aligned and logged even with unknown references."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        payload: dict[str, object] = {
            "Transactions": [
                {
                    "Action": "Deposit",
                    "Date": "01/10/2024",
                    "Description": "RS",
                    "Quantity": "2",
                    "TransactionDetails": [{"Details": {"VestFairMarketValue": "$100.00"}}],
                }
            ]
        }
        logs = TaxReportLogs()
        with (
            patch.object(
                reporter, "_detect_split_params", return_value=(date(2024, 6, 10), 10, False)
            ),
            patch.object(
                reporter, "_build_reference_context", return_value=({}, {}, {}, None, None)
            ),
            patch.object(reporter, "_raise_alignment_validation_errors", return_value=None),
        ):
            aligned = getattr(reporter, "_align_and_validate_payload")(payload, logs)

        aligned_tx = cast(dict[str, object], cast(list[object], aligned["Transactions"])[0])
        assert aligned_tx["Quantity"] == "20"
        assert any("Deposit" in line and "Quantity" in line for line in logs)

    def test_quantity_update_and_validation_raise_paths(self) -> None:
        """Cover quantity updates and basis-error raising path."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        update_qty = getattr(reporter, "_update_scaled_transaction_quantity")

        deposit_tx = {"Action": "Deposit", "Quantity": "2"}
        lapse_tx = {"Action": "Lapse", "Quantity": "2"}
        no_change_tx = {"Action": "Dividend", "Quantity": "2"}
        update_qty(deposit_tx, [], 10, False)
        update_qty(lapse_tx, [], 10, False)
        update_qty(no_change_tx, [], 10, False)
        assert deposit_tx["Quantity"] == "20"

        with (
            patch.object(reporter, "_validate_sale_amounts", return_value=[]),
            patch.object(reporter, "_validate_cost_basis", return_value=["bad basis"]),
        ):
            with self.assertRaisesRegex(ValueError, "bad basis"):
                getattr(reporter, "_raise_alignment_validation_errors")([])

    def _grouped_split_transactions(self) -> list[object]:
        base = date(2024, 1, 1)
        transactions: list[object] = [
            "bad-tx",
            {"Date": "bad", "TransactionDetails": []},
            {"Date": "01/01/2024", "TransactionDetails": "bad"},
        ]
        for idx in range(8):
            tx_date = (base + timedelta(days=idx)).strftime("%m/%d/%Y")
            old_scale = idx < 4
            unit = "$100.00" if old_scale else "$10.00"
            sale_price = "$1,000.00" if old_scale else "$100.00"
            transactions.append(
                {
                    "Date": tx_date,
                    "Action": "Sale",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "VestDate": "01/01/2023",
                                "VestFairMarketValue": unit,
                                "PurchaseDate": "01/01/2023",
                                "PurchasePrice": unit,
                                "SubscriptionDate": "01/01/2023",
                                "SubscriptionFairMarketValue": unit,
                                "SalePrice": sale_price,
                            }
                        }
                    ],
                }
            )
        transactions.append(
            {
                "Date": "01/20/2024",
                "Action": "Sale",
                "TransactionDetails": [
                    {"Details": {"VestDate": "02/02/2023", "VestFairMarketValue": "$90.00"}}
                ],
            }
        )
        return transactions

    def test_split_detection_from_grouped_transactions(self) -> None:
        """Detects split params from grouped pre/post reference values."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        transactions = self._grouped_split_transactions()
        groups = getattr(reporter, "_collect_scale_groups")(transactions)
        assert ("vest", "01/01/2023") in groups
        assert ("purchase", "01/01/2023") in groups
        assert ("subscription", "01/01/2023") in groups
        detected = getattr(reporter, "_detect_split_params")(transactions)
        assert detected is not None
        assert detected[1] == 10
        assert detected[2] is False

    def test_split_detection_from_sale_windows(self) -> None:
        """Covers sale-window detection, transient windows, and edge thresholds."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        base = date(2024, 1, 1)
        short_transactions: list[object] = [
            {
                "Date": "01/01/2024",
                "Action": "Sale",
                "TransactionDetails": [
                    {
                        "Details": {
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "$100.00",
                            "SalePrice": "$1,000.00",
                        }
                    }
                ],
            },
            {
                "Date": "01/02/2024",
                "Action": "Sale",
                "TransactionDetails": [
                    {
                        "Details": {
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "$10.00",
                            "SalePrice": "$100.00",
                        }
                    }
                ],
            },
        ]
        assert (
            getattr(reporter, "_detect_split_date_from_sales")(short_transactions, 10, False)
            is None
        )
        assert getattr(reporter, "_detect_split_params")(short_transactions) is not None

        transient_prices = [1000.0, 1000.0, 1000.0, 100.0, 100.0, 100.0, 1000.0, 1000.0, 1000.0]
        transient_transactions = [
            {
                "Date": (base + timedelta(days=idx)).strftime("%m/%d/%Y"),
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"SalePrice": f"${price:,.2f}"}}],
            }
            for idx, price in enumerate(transient_prices)
        ]
        assert (
            getattr(reporter, "_detect_split_date_from_sales")(transient_transactions, 10, False)
            is None
        )

        edge_prices = [100.0, 100.0, 100.0, 10.0, 10.0, 10.0]
        edge_transactions = [
            {
                "Date": (base + timedelta(days=idx)).strftime("%m/%d/%Y"),
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"SalePrice": f"${price:,.2f}"}}],
            }
            for idx, price in enumerate(edge_prices)
        ]
        assert (
            getattr(reporter, "_detect_split_date_from_sales")(edge_transactions, 2, False)
            is not None
        )
        assert (
            getattr(reporter, "_infer_transition_date_from_window")(
                [(base, 150.0), (base + timedelta(days=1), 160.0)],
                200.0,
                100.0,
                False,
            )
            == base
        )

    def test_fallback_split_helpers_and_series_parsing(self) -> None:
        """Exercises fallback split inference and sale-price series parsing guards."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        base = date(2024, 1, 1)
        assert (
            getattr(reporter, "_candidate_from_group")(
                [(base, 0.0), (base + timedelta(days=1), 0.0)]
            )
            is None
        )
        assert (
            getattr(reporter, "_candidate_from_group")(
                [(base, 10.0), (base + timedelta(days=1), 11.0)]
            )
            is None
        )
        assert (
            getattr(reporter, "_candidate_from_group")(
                [(base, 100.0), (base + timedelta(days=1), 80.0), (base + timedelta(days=2), 10.0)]
            )
            is not None
        )
        fallback_transactions: list[object] = [
            {"Date": "bad", "TransactionDetails": []},
            {"Date": "01/01/2024", "TransactionDetails": "bad"},
            {
                "Date": "01/01/2024",
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"VestFairMarketValue": "$20.00"}}],
            },
            {
                "Date": "01/02/2024",
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"VestFairMarketValue": "$2.00"}}],
            },
        ]
        fallback = getattr(reporter, "_detect_split_params_from_unit_values")(fallback_transactions)
        assert fallback is not None
        assert fallback[1] == 10
        assert fallback[2] is False
        assert getattr(reporter, "_first_positive_unit_value")({"VestFairMarketValue": ""}) is None

        sale_series_transactions: list[object] = [
            {"Date": "bad", "Action": "Sale", "TransactionDetails": []},
            {"Date": "01/01/2024", "Action": "Sale", "TransactionDetails": "bad"},
            {
                "Date": "01/02/2024",
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"SalePrice": "$0.00"}}],
            },
            {
                "Date": "01/03/2024",
                "Action": "Sale",
                "TransactionDetails": [{"Details": {"SalePrice": "$12.00"}}],
            },
        ]
        assert getattr(reporter, "_sale_price_series")(sale_series_transactions) == [
            (date(2024, 1, 3), 12.0)
        ]

    def test_parse_and_format_helpers(self) -> None:
        """Validates parsing/formatting helpers for numbers, money, and dates."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        assert getattr(reporter, "_factor_from_ratio")(1.0) is None
        assert getattr(reporter, "_factor_from_ratio")(1.79) is None
        assert getattr(reporter, "_factor_from_ratio")(2.6) is None
        assert getattr(reporter, "_parse_tx_date")(123) is None
        assert getattr(reporter, "_parse_tx_date")("bad-date") is None
        assert getattr(reporter, "_parse_number")(None) is None
        assert getattr(reporter, "_parse_number")(3) == 3.0
        assert getattr(reporter, "_parse_number")({}) is None
        assert getattr(reporter, "_parse_number")("  ") is None
        assert getattr(reporter, "_parse_number")("abc") is None
        assert getattr(reporter, "_parse_money")(None) == (None, "")
        assert getattr(reporter, "_parse_money")(3) == (3.0, "")
        assert getattr(reporter, "_parse_money")({}) == (None, "")
        assert getattr(reporter, "_parse_money")(" ") == (None, "")
        assert getattr(reporter, "_parse_money")("-$1,234.50") == (-1234.5, "$")
        assert getattr(reporter, "_parse_money")("123.40") == (123.4, "")
        assert getattr(reporter, "_parse_money")("$bad") == (None, "$")
        marker = object()
        marker2 = object()
        assert getattr(reporter, "_format_number_like")(1, 1.4) == 1
        assert getattr(reporter, "_format_number_like")(1.0, 1.4) == 1.4
        assert getattr(reporter, "_format_number_like")(marker, 1.4) is marker
        assert getattr(reporter, "_format_number_like")("1", 1.25) == "1.25"
        assert getattr(reporter, "_format_money_like")(1, 1.4, "$") == 1
        assert getattr(reporter, "_format_money_like")(1.0, 1.4, "$") == 1.4
        assert getattr(reporter, "_format_money_like")(marker2, 1.4, "$") is marker2

    def test_reference_context_and_add_reference_helpers(self) -> None:
        """Builds and updates reference maps used by the scaling heuristics."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        reference_context = getattr(reporter, "_build_reference_context")(
            [
                "bad",
                {"Date": "01/01/2024", "TransactionDetails": "bad"},
                {
                    "Date": "01/02/2024",
                    "Action": "Sale",
                    "TransactionDetails": [
                        {
                            "Details": {
                                "VestDate": "VD",
                                "VestFairMarketValue": "$10.00",
                                "PurchaseDate": "PD",
                                "PurchasePrice": "$8.00",
                                "SubscriptionDate": "SD",
                                "SubscriptionFairMarketValue": "$7.00",
                            }
                        },
                        {"Details": {"SalePrice": "$20.00"}},
                    ],
                },
            ],
            date(2024, 1, 1),
        )
        vest_map, purchase_map, subscription_map, post_sale_min, post_sale_max = reference_context
        assert vest_map["VD"] == 10.0
        assert purchase_map["PD"] == 8.0
        assert subscription_map["SD"] == 7.0
        assert post_sale_min == 20.0
        assert post_sale_max == 20.0
        getattr(reporter, "_update_reference_context_from_transaction")(
            {"TransactionDetails": "bad"},
            reference_context,
        )
        ref_map: dict[str, float] = {}
        getattr(reporter, "_add_reference_value")(
            ref_map,
            {"VestDate": "VD", "VestFairMarketValue": "$0.00"},
            "VestDate",
            "VestFairMarketValue",
        )
        assert not ref_map
        getattr(reporter, "_add_reference_value")(
            ref_map,
            {"VestDate": "VD", "VestFairMarketValue": "$12.00"},
            "VestDate",
            "VestFairMarketValue",
        )
        assert ref_map["VD"] == 12.0

    def _score_context(self) -> _ScaleContext:
        return _ScaleContext(
            split=_SplitParams(split_date=date(2024, 6, 10), factor=10, is_reverse=False),
            references=({"VD": 10.0}, {"PD": 10.0}, {"SD": 10.0}, 5.0, 15.0),
            default_scale_when_unknown=True,
        )

    def test_score_and_scaling_helpers(self) -> None:
        """Checks scoring decisions and low-level quantity/price scaling behavior."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        score_context = self._score_context()
        assert getattr(reporter, "_should_scale_detail")({}, "Dividend", score_context) is False
        with patch.object(reporter, "_detail_scale_scores", return_value=None):
            assert getattr(reporter, "_should_scale_detail")({}, "Sale", score_context) is True
        with patch.object(reporter, "_detail_scale_scores", return_value=(1, 1)):
            assert getattr(reporter, "_should_scale_detail")({}, "Sale", score_context) is True
        assert getattr(reporter, "_detail_scale_scores")({"VestDate": 1}, score_context) is None
        assert (
            getattr(reporter, "_detail_scale_scores")(
                {"VestDate": "VD", "VestFairMarketValue": ""},
                score_context,
            )
            is None
        )
        assert (
            getattr(reporter, "_detail_scale_scores")(
                {"VestDate": "UNKNOWN", "VestFairMarketValue": "$10"},
                score_context,
            )
            is None
        )
        assert getattr(reporter, "_detail_scale_scores")(
            {"VestDate": "VD", "VestFairMarketValue": "$100"},
            score_context,
        ) == (3, 0)
        assert getattr(reporter, "_detail_scale_scores")(
            {"VestDate": "VD", "VestFairMarketValue": "$10"},
            score_context,
        ) == (0, 3)
        assert getattr(reporter, "_detail_scale_scores")(
            {"VestDate": "VD", "VestFairMarketValue": "$40"},
            score_context,
        ) == (0, 0)
        assert getattr(reporter, "_detail_scale_scores")({"SalePrice": "$30"}, score_context) == (
            1,
            0,
        )
        assert (
            getattr(reporter, "_detail_scale_scores")({"SalePrice": "$10"}, score_context) is None
        )
        assert getattr(reporter, "_closer_to_scaled_value")(100.0, 10.0, 10, False) is True
        assert getattr(reporter, "_closer_to_scaled_value")(1.0, 10.0, 10, True) is True
        assert getattr(reporter, "_is_close")(10.2, 10.0) is True
        reverse_split = _SplitParams(split_date=date(2024, 6, 10), factor=10, is_reverse=True)
        assert getattr(reporter, "_price_range_suggests_scaling")(1.0, 10.0, 30.0, reverse_split)
        detail = {
            "Shares": "10",
            "NetSharesDeposited": "2",
            "SharesWithheld": "1",
            "SharesSold": "",
            "SalePrice": "$100.00",
            "PurchasePrice": "$50.00",
            "SubscriptionFairMarketValue": "$20.00",
            "VestFairMarketValue": "$40.00",
            "FairMarketValuePrice": "$30.00",
            "PurchaseFairMarketValue": "$10.00",
        }
        getattr(reporter, "_scale_detail")(detail, 10, True)
        assert detail["Shares"] == "1"
        assert detail["SalePrice"] == "$1,000"

    def test_sum_and_validation_helpers(self) -> None:
        """Covers share summation and sale-amount validation edge cases."""
        reporter = CharlesSchwabEmployeeSponsoredTaxReporter(_json_buf({}))
        summed = getattr(reporter, "_sum_sale_shares")(
            [1, {"Details": "bad"}, {"Details": {"Shares": ""}}, {"Details": {"Shares": "2"}}],
            "1",
        )
        assert summed == "2"
        assert getattr(reporter, "_scale_quantity_value")("bad", 10, False) == "bad"
        assert getattr(reporter, "_scale_quantity_value")("10", 10, True) == "1"
        sale_errors = getattr(reporter, "_validate_sale_amounts")(
            [
                "bad",
                {"Action": "Dividend"},
                {"Action": "Sale", "Amount": "", "TransactionDetails": []},
                {"Action": "Sale", "Amount": "$1.00", "TransactionDetails": "bad"},
                {
                    "Action": "Sale",
                    "Date": "01/01/2025",
                    "Amount": "$1.00",
                    "FeesAndCommissions": "$0.00",
                    "TransactionDetails": [
                        "bad",
                        {"Details": "bad"},
                        {"Details": {"Shares": "", "SalePrice": "$1.00"}},
                    ],
                },
                {
                    "Action": "Sale",
                    "Date": "01/02/2025",
                    "Amount": "$1.00",
                    "FeesAndCommissions": "$0.00",
                    "TransactionDetails": [{"Details": {"Shares": "2", "SalePrice": "$1.00"}}],
                },
            ]
        )
        assert sale_errors == ["01/02/2025 sale amount mismatch"]
        basis_errors = getattr(reporter, "_validate_cost_basis")(
            [
                "bad",
                {"Action": "Dividend"},
                {"Action": "Sale", "TransactionDetails": "bad"},
                {
                    "Action": "Sale",
                    "Date": "01/01/2025",
                    "TransactionDetails": [
                        "bad",
                        {"Details": "bad"},
                        {"Details": {"Shares": "", "TotalCostBasis": "$1.00"}},
                        {
                            "Details": {
                                "Shares": "1",
                                "TotalCostBasis": "$1.00",
                                "VestFairMarketValue": "",
                                "PurchasePrice": "",
                            }
                        },
                        {
                            "Details": {
                                "Shares": "2",
                                "TotalCostBasis": "$1.00",
                                "VestFairMarketValue": "$1.00",
                            }
                        },
                    ],
                },
            ]
        )
        assert basis_errors == ["01/01/2025 cost basis mismatch"]
