"""Reporter registry persistence and discovery helpers."""

import importlib
import os
from base64 import b64decode, b64encode
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import yaml

if TYPE_CHECKING:
    from polish_pit_calculator.tax_reporters import TaxReporter


class TaxReporterRegistry:
    """Singleton class for reporter-class registration and registry directory management."""

    _tax_reporter_class_defs: list[type["TaxReporter"]] = []
    _dir_env_var_name = "POLISH_PIT_CALCULATOR_REGISTRY_DIR"

    @classmethod
    def registry_dir(cls) -> Path:
        """Return path to persisted reporter-registry directory."""
        if path := os.environ.get(cls._dir_env_var_name):
            return Path(path).expanduser()
        return Path.home() / ".polish-pit-calculator"

    @classmethod
    def register(cls, class_def: type["TaxReporter"]) -> type["TaxReporter"]:
        """Register reporter class for app choices and factory resolution."""
        if class_def not in cls._tax_reporter_class_defs:
            cls._tax_reporter_class_defs.append(class_def)
        return class_def

    @classmethod
    def ls(cls) -> list[type["TaxReporter"]]:
        """Return registered reporter classes."""
        return sorted(
            cls._tax_reporter_class_defs,
            key=lambda class_def: class_def.name().casefold(),
        )

    @classmethod
    def unregister(cls, entry_id: str) -> None:
        """Delete persisted entry by ID."""
        entry_path = cls.registry_dir() / f"{entry_id}.yaml"
        entry_path.unlink()

    @classmethod
    def deserialize_all(
        cls,
        class_def: type["TaxReporter"] | None = None,
    ) -> list[tuple[str, "TaxReporter"]]:
        """Read all registry entries and deserialize reporter instances."""
        registry_dir = cls.registry_dir()
        if not registry_dir.is_dir():
            return []
        paths = sorted(registry_dir.iterdir(), key=lambda x: x.stat().st_mtime_ns)
        entry_ids = [path.stem for path in paths]
        tax_reporters = map(cls.deserialize, entry_ids)
        return [
            (entry_id, reporter)
            for entry_id, reporter in zip(entry_ids, tax_reporters)
            if class_def is None or isinstance(reporter, class_def)
        ]

    @classmethod
    def deserialize(cls, entry_id: str) -> "TaxReporter":
        """Instantiate reporter instance from persisted entry payload."""
        entry_path = cls.registry_dir() / f"{entry_id}.yaml"
        encoded = entry_path.read_text(encoding="utf-8").strip()
        decoded = b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
        entry = yaml.safe_load(decoded)
        module_name, _, class_name = entry["cls"].rpartition(".")
        module = importlib.import_module(module_name)
        class_def = getattr(module, class_name)
        return cast("TaxReporter", class_def(**entry["data"]))

    @classmethod
    def serialize(cls, reporter: "TaxReporter") -> str:
        """Persist reporter entry payload under generated registry id."""
        payload = {
            "cls": f"polish_pit_calculator.tax_reporters.{reporter.__class__.__name__}",
            "data": reporter.to_entry_data(),
        }
        entry_id = f"{uuid4().int % 1_000_000_000:09d}"
        registry_dir_mode = 0o700
        private_file_mode = 0o600
        entry_path = cls.registry_dir() / f"{entry_id}.yaml"
        registry_path = entry_path.parent
        registry_path.mkdir(parents=True, exist_ok=True, mode=registry_dir_mode)
        registry_path.chmod(registry_dir_mode)
        fd = os.open(entry_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, private_file_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            decoded = yaml.safe_dump(payload, sort_keys=False)
            encoded = b64encode(decoded.encode("utf-8")).decode("ascii")
            stream.write(encoded)
        entry_path.chmod(private_file_mode)
        return entry_id
