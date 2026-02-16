"""Caching helpers for local on-disk data used by report generators."""

import os
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Any, cast

import pandas as pd
import yaml

CACHE_DIR_ENV_VAR = "POLISH_PIT_CALCULATOR_CACHE_DIR"
REGISTRY_DIR_ENV_VAR = "POLISH_PIT_CALCULATOR_REGISTRY_DIR"


def _cache_dir() -> Path:
    if path := os.environ.get(CACHE_DIR_ENV_VAR):
        return Path(path).expanduser()
    return Path.home() / ".cache" / "polish-pit-calculator"


def _exchange_rates_cache_dir() -> Path:
    return _cache_dir() / "exchange-rates"


def _registry_dir() -> Path:
    """Return path to persisted reporter-registry directory."""
    if path := os.environ.get(REGISTRY_DIR_ENV_VAR):
        return Path(path).expanduser()
    return Path.home() / ".polish-pit-calculator"


def read_cached_year_dataframe(year: int) -> pd.DataFrame | None:
    """Read cached exchange rates table for a single year."""
    path = _exchange_rates_cache_dir() / f"{year}.csv"
    try:
        df = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
    except (FileNotFoundError, OSError, ValueError, pd.errors.ParserError):
        return None
    df.index = df.index.date
    return df


def write_cached_year_dataframe(year: int, df: pd.DataFrame) -> None:
    """Persist exchange rates table for a single year."""
    path = _exchange_rates_cache_dir() / f"{year}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="Date")


def read_registry_entry(entry_id: int) -> dict[str, Any]:
    """Read and decode one registry entry payload by entry id."""
    registry_path = _registry_dir()
    if registry_path.is_dir():
        registry_path.chmod(0o700)
    entry_path = registry_entry_path(entry_id)
    encoded_content = entry_path.read_text(encoding="utf-8").strip()
    decoded_payload = b64decode(encoded_content.encode("ascii"), validate=True).decode("utf-8")
    return cast(dict[str, Any], yaml.safe_load(decoded_payload))


def write_registry_entry(entry_id: int, payload: dict[str, Any]) -> Path:
    """Write one registry entry payload under the app registry directory."""
    registry_dir_mode = 0o700
    private_file_mode = 0o600
    registry_path = _registry_dir()
    registry_path.mkdir(parents=True, exist_ok=True, mode=registry_dir_mode)
    registry_path.chmod(registry_dir_mode)
    entry_path = registry_entry_path(entry_id)
    fd = os.open(entry_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, private_file_mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        payload_content = yaml.safe_dump(payload, sort_keys=False)
        encoded_content = b64encode(payload_content.encode("utf-8")).decode("ascii")
        handle.write(encoded_content)
    entry_path.chmod(private_file_mode)
    return entry_path


def registry_entry_path(entry_id: int) -> Path:
    """Return filesystem path for one registry entry id."""
    return _registry_dir() / f"{entry_id}.yaml"


def read_registry_entry_ids() -> list[int]:
    """Return sorted registry entry ids parsed from registry filenames."""
    registry_path = _registry_dir()
    if not registry_path.is_dir():
        return []
    registry_path.chmod(0o700)

    return [int(path.stem) for path in sorted(registry_path.iterdir())]
