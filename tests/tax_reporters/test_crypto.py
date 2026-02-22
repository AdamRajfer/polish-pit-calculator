"""Tests for crypto prompt reporter behavior."""

from unittest import TestCase

from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.tax_reporters import CryptoTaxReporter


class TestCryptoTaxReporter(TestCase):
    """Test crypto prompt reporter output."""

    def test_metadata_class_attributes(self) -> None:
        """Test reporter metadata class attributes for app routing."""
        self.assertEqual(CryptoTaxReporter.name(), "Crypto")
        self.assertEqual(
            set(CryptoTaxReporter.validators().keys()),
            {
                "year",
                "crypto_revenue",
                "crypto_cost",
                "crypto_cost_excess_from_previous_years",
            },
        )

    def test_details_and_entry_payload_are_built_from_constructor_values(self) -> None:
        """Test details and serialization payload for prompt reporter."""
        reporter = CryptoTaxReporter(2025, 10.0, 4.0, 1.0)
        self.assertEqual(reporter.details, "Year: 2025")
        self.assertEqual(
            reporter.to_entry_data(),
            {
                "year": 2025,
                "crypto_revenue": 10.0,
                "crypto_cost": 4.0,
                "crypto_cost_excess_from_previous_years": 1.0,
            },
        )

    def test_generate_builds_one_year_report(self) -> None:
        """Test reporter maps prompt values to one yearly TaxRecord."""
        reporter = CryptoTaxReporter(
            2025,
            50500.00,
            48000.50,
            700.0,
        )

        self.assertEqual(
            reporter.generate(),
            TaxReport(
                {
                    2025: TaxRecord(
                        crypto_revenue=50500.00,
                        crypto_cost=48000.50,
                        crypto_cost_excess_from_previous_years=700.0,
                    )
                }
            ),
        )
