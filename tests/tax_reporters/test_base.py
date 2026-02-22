"""Tests for TaxReporter base and TaxReporterRegistry."""

# pylint: disable=protected-access

import inspect
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from polish_pit_calculator.config import TaxReport, TaxReportLogs
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters import TaxReporter


class DummyReporter(TaxReporter):
    """Concrete reporter used for base-class tests."""

    @classmethod
    def name(cls) -> str:
        return "Dummy"

    @classmethod
    def validators(cls):
        return {"value": lambda raw: True}

    @property
    def details(self) -> str:
        return "Dummy"

    def to_entry_data(self) -> dict[str, Any]:
        return {"x": 1}

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        return TaxReport()


def test_tax_reporter_is_abstract() -> None:
    """TaxReporter should be marked as abstract."""
    assert inspect.isabstract(TaxReporter)


def test_tax_reporter_update_logs_appends_formatted_message() -> None:
    """update_logs should append one formatted string to provided sink."""
    reporter = DummyReporter()
    logs = TaxReportLogs()
    reporter.update_logs(
        date(2025, 1, 2),
        "example",
        "entry",
        changes=[{"name": "Value", "before": "old", "after": "new"}],
        logs=logs,
    )
    expected = (
        "[\x1b[36mDummy\x1b[0m] [\x1b[95m01/02/2025\x1b[0m] [\x1b[33mexample entry\x1b[0m]\n"
        " \x1b[36m•\x1b[0m Value: \x1b[31mold\x1b[0m -> \x1b[32mnew\x1b[0m"
    )
    assert logs == [expected]


def test_tax_reporter_update_logs_inserts_entries_in_ascending_date_order() -> None:
    """update_logs should keep sink chronologically ordered by log date."""
    reporter = DummyReporter()
    logs = TaxReportLogs()

    reporter.update_logs(
        date(2025, 1, 3),
        "example",
        "late",
        changes=[{"name": "Seq", "before": "2", "after": "3"}],
        logs=logs,
    )
    reporter.update_logs(
        date(2025, 1, 1),
        "example",
        "early",
        changes=[{"name": "Seq", "before": "0", "after": "1"}],
        logs=logs,
    )
    reporter.update_logs(
        date(2025, 1, 2),
        "example",
        "middle",
        changes=[{"name": "Seq", "before": "1", "after": "2"}],
        logs=logs,
    )

    assert "01/01/2025" in logs[0]
    assert "01/02/2025" in logs[1]
    assert "01/03/2025" in logs[2]


def test_tax_reporter_registry_register_skips_duplicates() -> None:
    """register should keep one class instance in registry list."""
    original = list(TaxReporterRegistry._tax_reporter_class_defs)
    TaxReporterRegistry._tax_reporter_class_defs = []
    try:
        TaxReporterRegistry.register(DummyReporter)
        TaxReporterRegistry.register(DummyReporter)
        assert TaxReporterRegistry._tax_reporter_class_defs == [DummyReporter]
    finally:
        TaxReporterRegistry._tax_reporter_class_defs = original


def test_tax_reporter_registry_ls_returns_registered_classes() -> None:
    """ls should return registered class definitions sorted by display name."""

    class ZReporter(TaxReporter):
        """Reporter used to verify class sorting by display name."""

        @classmethod
        def name(cls) -> str:
            return "Zulu"

        @classmethod
        def validators(cls):
            return {}

        @property
        def details(self) -> str:
            return ""

        def to_entry_data(self) -> dict[str, Any]:
            return {}

        def generate(self, logs: list[str] | None = None) -> TaxReport:
            return TaxReport()

    class AReporter(TaxReporter):
        """Reporter used to verify class sorting by display name."""

        @classmethod
        def name(cls) -> str:
            return "Alpha"

        @classmethod
        def validators(cls):
            return {}

        @property
        def details(self) -> str:
            return ""

        def to_entry_data(self) -> dict[str, Any]:
            return {}

        def generate(self, logs: list[str] | None = None) -> TaxReport:
            return TaxReport()

    original = list(TaxReporterRegistry._tax_reporter_class_defs)
    TaxReporterRegistry._tax_reporter_class_defs = [ZReporter, DummyReporter, AReporter]
    try:
        classes = TaxReporterRegistry.ls()
        assert classes == [AReporter, DummyReporter, ZReporter]
    finally:
        TaxReporterRegistry._tax_reporter_class_defs = original


def test_tax_reporter_registry_dir_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """registry_dir should use override from env var when set."""
    monkeypatch.setenv(TaxReporterRegistry._dir_env_var_name, "~/pit-registry")
    assert TaxReporterRegistry.registry_dir() == Path("~/pit-registry").expanduser()


def test_tax_reporter_registry_dir_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """registry_dir should default to hidden path under home directory."""
    monkeypatch.delenv(TaxReporterRegistry._dir_env_var_name, raising=False)
    with pytest.MonkeyPatch.context() as inner:
        inner.setattr(Path, "home", staticmethod(lambda: Path("/tmp/home")))
        assert TaxReporterRegistry.registry_dir() == Path("/tmp/home/.polish-pit-calculator")


def test_tax_reporter_unregister_removes_entry_file() -> None:
    """unregister should delete the persisted registry file by entry id."""
    with TemporaryDirectory() as tmp_dir:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv(TaxReporterRegistry._dir_env_var_name, tmp_dir)
            entry_id = TaxReporterRegistry.serialize(DummyReporter())
            entry_path = TaxReporterRegistry.registry_dir() / f"{entry_id}.yaml"
            assert entry_path.exists()
            TaxReporterRegistry.unregister(entry_id)
            assert not entry_path.exists()
