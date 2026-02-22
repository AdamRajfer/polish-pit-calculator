"""Comprehensive tests for core tax configuration models."""

from pathlib import Path
from typing import cast
from unittest import TestCase

import pandas as pd
from pandas.testing import assert_frame_equal

from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.tax_reporters import ApiTaxReporter, FileTaxReporter, TaxReporter


class TestTaxRecord(TestCase):
    """Test every computed property and helper on TaxRecord."""

    @staticmethod
    def _record() -> TaxRecord:
        """Return a record with non-zero values across all supported fields."""
        return TaxRecord(
            trade_revenue=100.0,
            trade_cost=40.0,
            trade_loss_from_previous_years=10.0,
            crypto_revenue=50.0,
            crypto_cost=30.0,
            crypto_cost_excess_from_previous_years=5.0,
            domestic_interest=20.0,
            foreign_interest=10.0,
            foreign_interest_withholding_tax=1.0,
            employment_revenue=1000.0,
            employment_cost=400.0,
            social_security_contributions=50.0,
            donations=40.0,
        )

    def _assert_dict_almost_equal(
        self,
        actual: dict[str, float],
        expected: dict[str, float],
        places: int = 10,
    ) -> None:
        """Assert full dict equality while using tolerance for float values."""
        self.assertEqual(set(actual), set(expected))
        for key, expected_value in expected.items():
            self.assertAlmostEqual(actual[key], expected_value, places=places)

    def test_all_computed_outputs_positive_case(self) -> None:
        """Test full set of computed outputs for profitable scenario."""
        record = self._record()
        actual = {
            "trade_profit": record.trade_profit,
            "trade_loss": record.trade_loss,
            "trade_tax": record.trade_tax,
            "crypto_profit": record.crypto_profit,
            "crypto_cost_excess": record.crypto_cost_excess,
            "crypto_tax": record.crypto_tax,
            "domestic_interest_tax": record.domestic_interest_tax,
            "foreign_interest_tax": record.foreign_interest_tax,
            "foreign_interest_remaining_tax": record.foreign_interest_remaining_tax,
            "employment_profit": record.employment_profit,
            "employment_profit_deduction": record.employment_profit_deduction,
            "total_profit": record.total_profit,
            "total_profit_deductions": record.total_profit_deductions,
            "solidarity_tax": record.solidarity_tax,
            "total_tax": record.total_tax,
        }
        expected = {
            "trade_profit": 50.0,
            "trade_loss": 0.0,
            "trade_tax": 9.5,
            "crypto_profit": 15.0,
            "crypto_cost_excess": 0.0,
            "crypto_tax": 2.85,
            "domestic_interest_tax": 3.8,
            "foreign_interest_tax": 1.9,
            "foreign_interest_remaining_tax": 0.9,
            "employment_profit": 600.0,
            "employment_profit_deduction": 36.0,
            "total_profit": 665.0,
            "total_profit_deductions": 86.0,
            "solidarity_tax": 0.0,
            "total_tax": 17.05,
        }
        self._assert_dict_almost_equal(actual, expected)

    def test_all_computed_outputs_loss_case(self) -> None:
        """Test full set of computed outputs for loss-heavy scenario."""
        record = TaxRecord(
            trade_revenue=10.0,
            trade_cost=30.0,
            trade_loss_from_previous_years=5.0,
            crypto_revenue=5.0,
            crypto_cost=30.0,
            crypto_cost_excess_from_previous_years=2.0,
            foreign_interest=10.0,
            foreign_interest_withholding_tax=10.0,
            employment_revenue=100.0,
            employment_cost=120.0,
            social_security_contributions=0.0,
            donations=0.0,
        )
        actual = {
            "trade_profit": record.trade_profit,
            "trade_loss": record.trade_loss,
            "trade_tax": record.trade_tax,
            "crypto_profit": record.crypto_profit,
            "crypto_cost_excess": record.crypto_cost_excess,
            "crypto_tax": record.crypto_tax,
            "domestic_interest_tax": record.domestic_interest_tax,
            "foreign_interest_tax": record.foreign_interest_tax,
            "foreign_interest_remaining_tax": record.foreign_interest_remaining_tax,
            "employment_profit": record.employment_profit,
            "employment_profit_deduction": record.employment_profit_deduction,
            "total_profit": record.total_profit,
            "total_profit_deductions": record.total_profit_deductions,
            "solidarity_tax": record.solidarity_tax,
            "total_tax": record.total_tax,
        }
        expected = {
            "trade_profit": 0.0,
            "trade_loss": 25.0,
            "trade_tax": 0.0,
            "crypto_profit": 0.0,
            "crypto_cost_excess": 27.0,
            "crypto_tax": 0.0,
            "domestic_interest_tax": 0.0,
            "foreign_interest_tax": 1.9,
            "foreign_interest_remaining_tax": 0.0,
            "employment_profit": -20.0,
            "employment_profit_deduction": -1.2,
            "total_profit": -20.0,
            "total_profit_deductions": -1.2,
            "solidarity_tax": 0.0,
            "total_tax": 0.0,
        }
        self._assert_dict_almost_equal(actual, expected)

    def test_solidarity_tax_above_threshold(self) -> None:
        """Test solidarity-tax branch above threshold using full output dict."""
        record = TaxRecord(employment_revenue=1_200_000.0, employment_cost=100_000.0)
        actual = {
            "employment_profit": record.employment_profit,
            "total_profit": record.total_profit,
            "total_profit_deductions": record.total_profit_deductions,
            "solidarity_tax": record.solidarity_tax,
            "total_tax": record.total_tax,
        }
        expected = {
            "employment_profit": 1_100_000.0,
            "total_profit": 1_100_000.0,
            "total_profit_deductions": 0.0,
            "solidarity_tax": 4000.0,
            "total_tax": 4000.0,
        }
        self._assert_dict_almost_equal(actual, expected)

    def test_to_dict_contains_all_expected_rows(self) -> None:
        """Test to_dict exact output values."""
        expected = {
            "Trade Revenue": 100.0,
            "Trade Cost": 40.0,
            "Trade Loss from Previous Years": 10.0,
            "Trade Loss": 0.0,
            "Crypto Revenue": 50.0,
            "Crypto Cost": 30.0,
            "Crypto Cost Excess from Previous Years": 5.0,
            "Crypto Cost Excess": 0.0,
            "Domestic Interest Tax": 3.8,
            "Foreign Interest Tax": 1.9,
            "Foreign Interest Withholding Tax": 1.0,
            "Employment Profit Deduction": 36.0,
            "Total Profit": 665.0,
            "Total Profit Deductions": 86.0,
            "Solidarity Tax": 0.0,
            "Total Tax": 17.05,
        }
        self._assert_dict_almost_equal(self._record().to_dict(), expected)

    def test_get_name_to_pit_label_mapping_shape(self) -> None:
        """Test PIT mapping exact output dictionary."""
        expected_pairs = (
            ("Trade Revenue", "PIT-38/C20"),
            ("Trade Cost", "PIT-38/C21"),
            ("Trade Loss from Previous Years", "PIT-38/D28"),
            ("Trade Loss", "PIT-38/D28 - Next Year"),
            ("Crypto Revenue", "PIT-38/E34"),
            ("Crypto Cost", "PIT-38/E35"),
            ("Crypto Cost Excess from Previous Years", "PIT-38/E36"),
            ("Crypto Cost Excess", "PIT-38/E36 - Next Year"),
            ("Domestic Interest Tax", "PIT-38/G44"),
            ("Foreign Interest Tax", "PIT-38/G45"),
            ("Foreign Interest Withholding Tax", "PIT-38/G46"),
            ("Employment Profit Deduction", "PIT/O/B11 -> PIT-37/F124"),
            ("Total Profit", "DSF-1/C18 - If Solidarity Tax > 0.00"),
            ("Total Profit Deductions", "DSF-1/C19 - If Solidarity Tax > 0.00"),
            ("Solidarity Tax", ""),
            ("Total Tax", ""),
        )
        expected = dict(expected_pairs)
        self.assertEqual(TaxRecord.get_name_to_pit_label_mapping(), expected)

    def test_tax_record_add_sums_all_fields(self) -> None:
        """Test TaxRecord addition is field-wise across all dataclass fields."""
        left = TaxRecord(trade_revenue=1.0, domestic_interest=2.0)
        right = TaxRecord(trade_revenue=3.0, domestic_interest=4.0)
        self.assertEqual(left + right, TaxRecord(trade_revenue=4.0, domestic_interest=6.0))

    def test_tax_record_eq_with_other_type_returns_false(self) -> None:
        """Test TaxRecord equality against other types returns False."""
        self.assertFalse(TaxRecord() == object())


class TestTaxReport(TestCase):
    """Test TaxReport container semantics and dataframe conversion."""

    def test_getitem_returns_empty_record_for_unknown_year(self) -> None:
        """Test unknown year access returns default empty TaxRecord."""
        report = TaxReport()
        self.assertEqual(report[2099], TaxRecord())

    def test_setitem_rejects_duplicate_year(self) -> None:
        """Test setting same year twice raises explicit error."""
        report = TaxReport()
        report[2024] = TaxRecord(trade_revenue=1.0)
        with self.assertRaisesRegex(ValueError, "already registered"):
            report[2024] = TaxRecord(trade_revenue=2.0)

    def test_items_returns_year_record_pairs(self) -> None:
        """Test items returns year-record tuples from internal mapping."""
        report = TaxReport({2024: TaxRecord(trade_revenue=1.0)})
        self.assertEqual(report.items(), [(2024, TaxRecord(trade_revenue=1.0))])

    def test_add_merges_overlap_and_disjoint_years(self) -> None:
        """Test merge behavior for left-only, right-only and overlapping years."""
        left = TaxReport(
            {
                2023: TaxRecord(trade_revenue=10.0),
                2024: TaxRecord(trade_revenue=20.0),
            }
        )
        right = TaxReport(
            {
                2024: TaxRecord(trade_revenue=5.0),
                2025: TaxRecord(trade_revenue=7.0),
            }
        )
        merged = left + right
        expected = TaxReport(
            {
                2023: TaxRecord(trade_revenue=10.0),
                2024: TaxRecord(trade_revenue=25.0),
                2025: TaxRecord(trade_revenue=7.0),
            }
        )
        self.assertEqual(merged, expected)

    def test_radd_supports_builtin_sum_default_seed(self) -> None:
        """Test sum([TaxReport, ...]) works by handling the implicit zero seed."""
        reports = [
            TaxReport({2024: TaxRecord(trade_revenue=1.0)}),
            TaxReport({2024: TaxRecord(trade_revenue=2.0)}),
            TaxReport({2025: TaxRecord(trade_revenue=3.0)}),
        ]
        actual = sum(reports)
        expected = TaxReport(
            {
                2024: TaxRecord(trade_revenue=3.0),
                2025: TaxRecord(trade_revenue=3.0),
            }
        )
        self.assertEqual(actual, expected)

    def test_radd_non_zero_seed_propagates_type_error_via_operator(self) -> None:
        """Test unsupported additions still fail when non-zero seed is used."""
        with self.assertRaises(TypeError):
            _ = object() + TaxReport()

    def test_radd_handles_tax_report_left_operand_via_operator_dispatch(self) -> None:
        """Test __radd__ TaxReport branch through `+` without direct dunder call."""

        class LeftOperand(TaxReport):
            """Custom left operand that defers addition to right operand."""

            def __add__(self, other: TaxReport) -> TaxReport:
                return cast(TaxReport, NotImplemented)

        left = LeftOperand({2024: TaxRecord(trade_revenue=1.0)})
        right = TaxReport({2024: TaxRecord(trade_revenue=2.0)})
        self.assertEqual(left + right, TaxReport({2024: TaxRecord(trade_revenue=3.0)}))

    def test_to_dataframe_formats_values_and_joins_pit_labels(self) -> None:
        """Test dataframe output includes PIT labels and 2-decimal formatted strings."""
        record = TaxRecord(
            trade_revenue=1234.5,
            trade_cost=10.0,
            domestic_interest=1.0,
        )
        report = TaxReport({2024: record})
        actual = report.to_dataframe()
        expected = pd.Series(
            TaxRecord.get_name_to_pit_label_mapping(),
            name="PIT",
        ).to_frame()
        expected_2024 = pd.Series(
            {key: f"{value:,.2f}" for key, value in record.to_dict().items()},
            name=2024,
        ).to_frame()
        expected = expected.join(expected_2024)
        assert_frame_equal(actual, expected)


class TestTaxReporterAbstract(TestCase):
    """Test abstract TaxReporter contract behavior."""

    def test_tax_reporter_is_abstract(self) -> None:
        """Test abstract base class cannot be instantiated directly."""

        class DummyFileReporter(FileTaxReporter):
            """Concrete file reporter for abstract-base behavior assertions."""

            @classmethod
            def extension(cls) -> str:
                return ".csv"

            @classmethod
            def name(cls) -> str:
                return "Dummy File"

            def generate(self, logs: list[str] | None = None) -> TaxReport:
                return TaxReport()

        class DummyApiReporter(ApiTaxReporter):
            """Concrete API reporter for abstract-base behavior assertions."""

            @classmethod
            def name(cls) -> str:
                return "Dummy API"

            def generate(self, logs: list[str] | None = None) -> TaxReport:
                return TaxReport()

        self.assertEqual(
            TaxReporter.__abstractmethods__,
            frozenset({"details", "generate", "name", "to_entry_data", "validators"}),
        )
        reporter = DummyFileReporter(Path("x.csv"))
        self.assertEqual(reporter.path, Path("x.csv").resolve())
        self.assertEqual(reporter.details, "File: x.csv")
        self.assertEqual(DummyFileReporter.extension(), ".csv")
        api_reporter = DummyApiReporter(123, " token ")
        self.assertEqual(api_reporter.query_id, "123")
        self.assertEqual(api_reporter.token, "token")
