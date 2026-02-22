"""Trade prompt tax reporter implementation."""

from typing import Any

from polish_pit_calculator.config import PromptValidator, TaxRecord, TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.base import TaxReporter
from polish_pit_calculator.validators import validate_amount, validate_year


@TaxReporterRegistry.register
class TradeTaxReporter(TaxReporter):
    """Build trade tax report from prompt-collected values."""

    def __init__(
        self,
        year: int | str,
        trade_revenue: float | str,
        trade_cost: float | str,
        trade_loss_from_previous_years: float | str,
    ) -> None:
        """Store prompt-provided yearly trade values."""
        super().__init__()
        self.year = int(year)
        self.trade_revenue = float(trade_revenue)
        self.trade_cost = float(trade_cost)
        self.trade_loss_from_previous_years = float(trade_loss_from_previous_years)

    @classmethod
    def name(cls) -> str:
        """Return reporter name shown in app choices."""
        return "Trade"

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators for trade prompts."""
        return {
            "year": validate_year,
            "trade_revenue": validate_amount,
            "trade_cost": validate_amount,
            "trade_loss_from_previous_years": validate_amount,
        }

    @property
    def details(self) -> str:
        """Return registry details row for trade reporter."""
        return f"Year: {self.year}"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Build one-year tax report entry from stored trade values."""
        report = TaxReport()
        report[self.year] = TaxRecord(
            trade_revenue=self.trade_revenue,
            trade_cost=self.trade_cost,
            trade_loss_from_previous_years=self.trade_loss_from_previous_years,
        )
        return report

    def to_entry_data(self) -> dict[str, Any]:
        """Build trade reporter payload for persisted entry data."""
        return {
            "year": self.year,
            "trade_revenue": self.trade_revenue,
            "trade_cost": self.trade_cost,
            "trade_loss_from_previous_years": self.trade_loss_from_previous_years,
        }
