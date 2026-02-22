"""Tests for file reporter base class."""

from pathlib import Path
from unittest.mock import patch

from polish_pit_calculator.config import TaxReport
from polish_pit_calculator.tax_reporters import FileTaxReporter
from polish_pit_calculator.tax_reporters import file as file_module


class DummyFileReporter(FileTaxReporter):
    """Concrete file reporter for base behavior tests."""

    @classmethod
    def extension(cls) -> str:
        """Return accepted extension for this test reporter."""
        return ".txt"

    @classmethod
    def name(cls) -> str:
        """Return reporter name."""
        return "Dummy File"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return empty report."""
        return TaxReport()


class DummyCsvReporter(FileTaxReporter):
    """Concrete CSV reporter for extension validation tests."""

    @classmethod
    def extension(cls) -> str:
        """Return accepted extension for this test reporter."""
        return ".csv"

    @classmethod
    def name(cls) -> str:
        """Return reporter name."""
        return "Dummy CSV"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return empty report."""
        return TaxReport()


class DummyJsonReporter(FileTaxReporter):
    """Concrete JSON reporter for extension validation tests."""

    @classmethod
    def extension(cls) -> str:
        """Return accepted extension for this test reporter."""
        return ".json"

    @classmethod
    def name(cls) -> str:
        """Return reporter name."""
        return "Dummy JSON"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return empty report."""
        return TaxReport()


def test_file_tax_reporter_accepts_string_path() -> None:
    """File reporter should accept string path and store it as `Path`."""
    reporter = DummyFileReporter("report.csv")
    assert reporter.path == Path("report.csv").resolve()


def test_file_tax_reporter_details_and_entry_payload_use_path_name() -> None:
    """File reporter should expose filename details and serialized path payload."""
    reporter = DummyFileReporter("report.csv")
    assert reporter.details == "File: report.csv"
    assert reporter.to_entry_data() == {"path": str(Path("report.csv").resolve())}


def test_file_tax_reporter_validators_include_path_key() -> None:
    """File reporter validators map should include constructor `path` validation."""
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        validators = DummyCsvReporter.validators()
    assert set(validators) == {"path"}


def test_file_tax_reporter_extension_validation_for_txt(tmp_path: Path) -> None:
    """TXT reporter should enforce `.txt` extension."""
    txt_file = tmp_path / "a.txt"
    csv_file = tmp_path / "a.csv"
    txt_file.write_text("x", encoding="utf-8")
    csv_file.write_text("x", encoding="utf-8")

    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        validate = DummyFileReporter.validators()["path"]
    assert validate(str(txt_file)) is True
    assert validate(str(csv_file)) == "Only .txt files are supported."


def test_csv_tax_reporter_extension_validation(tmp_path: Path) -> None:
    """CSV reporter should enforce `.csv` extension."""
    csv_file = tmp_path / "a.csv"
    txt_file = tmp_path / "a.txt"
    csv_file.write_text("x", encoding="utf-8")
    txt_file.write_text("x", encoding="utf-8")

    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        validate = DummyCsvReporter.validators()["path"]
    assert validate(str(csv_file)) is True
    assert validate(str(txt_file)) == "Only .csv files are supported."


def test_json_tax_reporter_extension_validation(tmp_path: Path) -> None:
    """JSON reporter should enforce `.json` extension."""
    json_file = tmp_path / "a.json"
    txt_file = tmp_path / "a.txt"
    json_file.write_text("x", encoding="utf-8")
    txt_file.write_text("x", encoding="utf-8")

    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        validate = DummyJsonReporter.validators()["path"]
    assert validate(str(json_file)) is True
    assert validate(str(txt_file)) == "Only .json files are supported."


def test_file_validator_rejects_duplicate_registered_path(tmp_path: Path) -> None:
    """File validator should reject paths already registered for reporter type."""
    path = tmp_path / "already.csv"
    path.write_text("x", encoding="utf-8")
    entries = [("000000001", DummyCsvReporter(path))]
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=entries):
        validate = DummyCsvReporter.validators()["path"]
    assert validate(str(path)) == "File already registered for this report type."
