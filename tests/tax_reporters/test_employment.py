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

    def test_details_omits_zero_amount_fields(self) -> None:
        """Test details skip zero-value fields while keeping year and non-zero values."""
        reporter = EmploymentTaxReporter(2025, 1.0, 0.0, 3.0, 0.0)
        self.assertEqual(
            reporter.details,
            "Year: 2025 Employment Revenue: 1.00 Social Security Contributions: 3.00",
        )

    def test_details_can_skip_revenue_and_social_security_when_zero(self) -> None:
        """Test details keep remaining non-zero values when selected fields are zero."""
        reporter = EmploymentTaxReporter(2025, 0.0, 2.0, 0.0, 4.0)
        self.assertEqual(
            reporter.details,
            "Year: 2025 Employment Cost: 2.00 Donations: 4.00",
        )
