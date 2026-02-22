"""API-backed reporter base class."""

from typing import Any

from polish_pit_calculator.config import PromptValidator
from polish_pit_calculator.tax_reporters.base import TaxReporter
from polish_pit_calculator.validators import validate_query_id, validate_token


class ApiTaxReporter(TaxReporter):
    """Base class for API-backed reporters."""

    def __init__(self, query_id: int | str, token: str) -> None:
        """Store API credentials used by concrete API reporters."""
        super().__init__()
        self.query_id = str(query_id).strip()
        self.token = str(token).strip()

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators for API reporter prompts."""
        return {"query_id": validate_query_id, "token": validate_token}

    @property
    def details(self) -> str:
        """Return registry details row for API reporter."""
        return f"Query ID: {self.query_id}"

    def to_entry_data(self) -> dict[str, Any]:
        """Build API-backed reporter payload for persisted entry data."""
        return {"query_id": self.query_id, "token": self.token}
