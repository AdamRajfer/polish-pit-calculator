"""Tests for trade prompt reporter behavior."""

from unittest import TestCase

from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.tax_reporters import TradeTaxReporter


class TestTradeTaxReporter(TestCase):
    """Test trade prompt reporter output."""

    def test_metadata_class_attributes(self) -> None:
        """Test reporter metadata class attributes for app routing."""
        self.assertEqual(TradeTaxReporter.name(), "Trade")
        self.assertEqual(
            set(TradeTaxReporter.validators().keys()),
            {
                "year",
                "trade_revenue",
                "trade_cost",
                "trade_loss_from_previous_years",
            },
        )

    def test_details_and_entry_payload_are_built_from_constructor_values(self) -> None:
        """Test details and serialization payload for prompt reporter."""
        reporter = TradeTaxReporter(2025, 10.0, 4.0, 1.0)
        self.assertEqual(
            reporter.details,
            (
                "Year: 2025 "
                "Trade Revenue: 10.00 "
                "Trade Cost: 4.00 "
                "Trade Loss From Previous Years: 1.00"
            ),
        )
        self.assertEqual(
            reporter.to_entry_data(),
            {
                "year": 2025,
                "trade_revenue": 10.0,
                "trade_cost": 4.0,
                "trade_loss_from_previous_years": 1.0,
            },
        )

    def test_details_omits_zero_amount_fields(self) -> None:
        """Test details skip zero-value fields while keeping year and non-zero values."""
        reporter = TradeTaxReporter(2025, 10.0, 0.0, 0.0)
        self.assertEqual(reporter.details, "Year: 2025 Trade Revenue: 10.00")

    def test_details_can_skip_trade_revenue_when_zero(self) -> None:
        """Test details keep remaining non-zero values when trade revenue is zero."""
        reporter = TradeTaxReporter(2025, 0.0, 4.0, 1.0)
        self.assertEqual(
            reporter.details,
            "Year: 2025 Trade Cost: 4.00 Trade Loss From Previous Years: 1.00",
        )

    def test_generate_builds_one_year_report(self) -> None:
        """Test reporter maps prompt values to one yearly TaxRecord."""
        reporter = TradeTaxReporter(
            2025,
            100000.50,
            74500.25,
            1200.0,
        )

        self.assertEqual(
            reporter.generate(),
            TaxReport(
                {
                    2025: TaxRecord(
                        trade_revenue=100000.50,
                        trade_cost=74500.25,
                        trade_loss_from_previous_years=1200.0,
                    )
                }
            ),
        )
