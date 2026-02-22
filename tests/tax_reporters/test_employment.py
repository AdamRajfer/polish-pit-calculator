"""Tests for employment prompt reporter behavior."""

from unittest import TestCase

from polish_pit_calculator.config import TaxRecord, TaxReport
from polish_pit_calculator.tax_reporters import EmploymentTaxReporter


class TestEmploymentTaxReporter(TestCase):
    """Test employment prompt reporter output."""

    def test_metadata_class_attributes(self) -> None:
        """Test reporter metadata class attributes for app routing."""
        self.assertEqual(EmploymentTaxReporter.name(), "Employment")

    def test_generate_builds_one_year_report(self) -> None:
        """Test reporter maps prompt values to one yearly TaxRecord."""
        reporter = EmploymentTaxReporter(
            2025,
            25025.93,
            49490.99,
            1000.0,
            300.0,
        )

        self.assertEqual(
            reporter.generate(),
            TaxReport(
                {
                    2025: TaxRecord(
                        employment_revenue=25025.93,
                        employment_cost=49490.99,
                        social_security_contributions=1000.0,
                        donations=300.0,
                    )
                }
            ),
        )
