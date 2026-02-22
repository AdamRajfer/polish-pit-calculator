"""Tests for ApiTaxReporter base class."""

import inspect

from polish_pit_calculator.config import TaxReport
from polish_pit_calculator.tax_reporters import ApiTaxReporter


class DummyApiReporter(ApiTaxReporter):
    """Concrete API reporter for base behavior tests."""

    @classmethod
    def name(cls) -> str:
        """Return reporter name."""
        return "Dummy API"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return empty report."""
        return TaxReport()


def test_api_tax_reporter_is_abstract() -> None:
    """ApiTaxReporter should be marked as abstract."""
    assert inspect.isabstract(ApiTaxReporter)


def test_api_tax_reporter_init_stores_credentials() -> None:
    """Concrete API reporter should store query-id and token values."""
    reporter = DummyApiReporter("query", "token")
    assert reporter.query_id == "query"
    assert reporter.token == "token"


def test_api_tax_reporter_default_validators_accept_values() -> None:
    """Base API prepare flow should expose non-empty validators for query id and token."""
    validators = DummyApiReporter.validators()
    query_validator = validators["query_id"]
    assert query_validator("") == "Query ID is required."
    assert query_validator("value") is True
    assert query_validator("123") is True

    token_validator = validators["token"]
    assert token_validator("") == "Token is required."
    assert token_validator("token") is True


def test_api_tax_reporter_to_entry_data_returns_credentials() -> None:
    """API reporter should serialize query id and token in entry payload."""
    reporter = DummyApiReporter("abc", "secret")
    assert reporter.to_entry_data() == {"query_id": "abc", "token": "secret"}
