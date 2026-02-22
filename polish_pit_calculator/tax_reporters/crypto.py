"""Crypto prompt tax reporter implementation."""

from typing import Any

from polish_pit_calculator.config import PromptValidator, TaxRecord, TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.base import TaxReporter
from polish_pit_calculator.validators import validate_amount, validate_year


@TaxReporterRegistry.register
class CryptoTaxReporter(TaxReporter):
    """Build crypto tax report from prompt-collected values."""

    def __init__(
        self,
        year: int | str,
        crypto_revenue: float | str,
        crypto_cost: float | str,
        crypto_cost_excess_from_previous_years: float | str,
    ) -> None:
        """Store prompt-provided yearly crypto values."""
        super().__init__()
        self.year = int(year)
        self.crypto_revenue = float(crypto_revenue)
        self.crypto_cost = float(crypto_cost)
        self.crypto_cost_excess_from_previous_years = float(crypto_cost_excess_from_previous_years)

    @classmethod
    def name(cls) -> str:
        """Return reporter name shown in app choices."""
        return "Crypto"

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators for crypto prompts."""
        return {
            "year": validate_year,
            "crypto_revenue": validate_amount,
            "crypto_cost": validate_amount,
            "crypto_cost_excess_from_previous_years": validate_amount,
        }

    @property
    def details(self) -> str:
        """Return registry details row for crypto reporter."""
        return f"Year: {self.year}"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Build one-year tax report entry from stored crypto values."""
        report = TaxReport()
        report[self.year] = TaxRecord(
            crypto_revenue=self.crypto_revenue,
            crypto_cost=self.crypto_cost,
            crypto_cost_excess_from_previous_years=self.crypto_cost_excess_from_previous_years,
        )
        return report

    def to_entry_data(self) -> dict[str, Any]:
        """Build crypto reporter payload for persisted entry data."""
        return {
            "year": self.year,
            "crypto_revenue": self.crypto_revenue,
            "crypto_cost": self.crypto_cost,
            "crypto_cost_excess_from_previous_years": self.crypto_cost_excess_from_previous_years,
        }
