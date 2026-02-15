"""Caching helpers for local on-disk data used by report generators."""

import os
from pathlib import Path

import pandas as pd

CACHE_DIR_ENV_VAR = "POLISH_PIT_CALCULATOR_CACHE_DIR"


def _cache_dir() -> Path:
    if path := os.environ.get(CACHE_DIR_ENV_VAR):
        return Path(path).expanduser()
    return Path.home() / ".cache" / "polish-pit-calculator"


def _exchange_rates_cache_dir() -> Path:
    return _cache_dir() / "exchange-rates"


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
