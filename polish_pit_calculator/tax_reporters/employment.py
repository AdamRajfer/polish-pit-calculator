"""Employment prompt tax reporter implementation."""

from typing import Any

from polish_pit_calculator.config import PromptValidator, TaxRecord, TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.base import TaxReporter
from polish_pit_calculator.validators import validate_amount, validate_year


@TaxReporterRegistry.register
class EmploymentTaxReporter(TaxReporter):
    """Build employment tax report from prompt-collected values."""

    def __init__(
        self,
        year: int | str,
        employment_revenue: float | str,
        employment_cost: float | str,
        social_security_contributions: float | str,
        donations: float | str,
    ) -> None:
        """Store prompt-provided yearly employment values."""
        super().__init__()
        self.year = int(year)
        self.employment_revenue = float(employment_revenue)
        self.employment_cost = float(employment_cost)
        self.social_security_contributions = float(social_security_contributions)
        self.donations = float(donations)

    @classmethod
    def name(cls) -> str:
        """Return reporter name shown in app choices."""
        return "Employment"

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators for employment prompts."""
        return {
            "year": validate_year,
            "employment_revenue": validate_amount,
            "employment_cost": validate_amount,
            "social_security_contributions": validate_amount,
            "donations": validate_amount,
        }

    @property
    def details(self) -> str:
        """Return registry details row for employment reporter."""
        return f"Year: {self.year}"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Build one-year tax report entry from stored employment values."""
        report = TaxReport()
        report[self.year] = TaxRecord(
            employment_revenue=self.employment_revenue,
            employment_cost=self.employment_cost,
            social_security_contributions=self.social_security_contributions,
            donations=self.donations,
        )
        return report

    def to_entry_data(self) -> dict[str, Any]:
        """Build employment reporter payload for persisted entry data."""
        return {
            "year": self.year,
            "employment_revenue": self.employment_revenue,
            "employment_cost": self.employment_cost,
            "social_security_contributions": self.social_security_contributions,
            "donations": self.donations,
        }
