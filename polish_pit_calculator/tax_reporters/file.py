"""File-backed reporter base class."""

from abc import abstractmethod
from functools import partial
from pathlib import Path
from typing import Any, cast

from polish_pit_calculator.config import PromptValidator
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters.base import TaxReporter


def _validate_file_input(raw: str, extension: str, registered_paths: set[Path]) -> bool | str:
    """Validate non-empty, existing, extension-matching, non-duplicate file input."""
    if not (text := raw.strip()):
        return "This field is required."
    if not (path := Path(text).expanduser().resolve()).is_file():
        return "Path must be a file."
    if path.suffix.lower() != extension:
        return f"Only {extension} files are supported."
    if path in registered_paths:
        return "File already registered for this report type."
    return True


class FileTaxReporter(TaxReporter):
    """Base class for file-backed reporters."""

    def __init__(self, path: Path | str) -> None:
        """Store one input file path."""
        super().__init__()
        self.path = Path(path).expanduser().resolve()

    @classmethod
    @abstractmethod
    def extension(cls) -> str:
        """Return reporter file extension used for validation."""

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators for file reporter prompts."""
        validator = partial(
            _validate_file_input,
            extension=cls.extension(),
            registered_paths={
                cast(FileTaxReporter, x[1]).path for x in TaxReporterRegistry.deserialize_all(cls)
            },
        )
        return {"path": validator}

    @property
    def details(self) -> str:
        """Return registry details row for file-backed reporter."""
        return f"File: {self.path.name}"

    def to_entry_data(self) -> dict[str, Any]:
        """Build file-backed reporter payload for persisted entry data."""
        return {"path": str(self.path)}
