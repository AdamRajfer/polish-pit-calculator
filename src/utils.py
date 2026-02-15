"""Utilities for exchange-rate access and shared data-loading helpers."""

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Callable

import pandas as pd

from src.caches import read_cached_year_dataframe, write_cached_year_dataframe


@dataclass(slots=True)
class _ExchangeRatesCacheState:
    """In-memory cache metadata and exchange-rate payload."""

    exchange_rates: dict[str, dict[date, float]] | None = None
    min_year: int | None = None
    current_year: int | None = None


_CACHE_STATE = _ExchangeRatesCacheState()


def _try_to_cast_string_to_float(value: object) -> float | None:
    """Convert NBP decimal string using comma separator to float."""
    try:
        assert "," in str(value)
        return float(str(value).replace(",", "."))
    except (ValueError, AssertionError, TypeError):
        return None


def _fetch_exchange_rates_for_year(year: int) -> pd.DataFrame:
    """Fetch one year of NBP exchange-rate table A."""
    df = (
        pd.read_csv(
            ("https://static.nbp.pl/dane/kursy/Archiwum/" f"archiwum_tab_a_{year}.csv"),
            delimiter=";",
            encoding="iso-8859-2",
            header=0,
            skiprows=[1],
        )
        .set_index("data")
        .map(_try_to_cast_string_to_float)
        .dropna(axis=1, how="all")
        .dropna(axis=0, how="all")
        .astype(float)
        .rename_axis(index="Date")
    )
    df.index = pd.to_datetime(df.index).date
    df.columns = [f"_{x}" if x[0].isdigit() else x for x in df.columns]
    return df


def _load_year_dataframe(
    year: int,
    current_year: int,
    year_loader: Callable[[int], pd.DataFrame],
    read_cached: Callable[[int], pd.DataFrame | None],
    write_cached: Callable[[int, pd.DataFrame], None],
) -> pd.DataFrame:
    """Load rates for a year, using cache only for past years."""
    if year < current_year and (cached := read_cached(year)) is not None:
        return cached
    df = year_loader(year)
    if year < current_year:
        write_cached(year, df)
    return df


def _fetch_exchange_rates(
    min_year: int,
    year_loader: Callable[[int], pd.DataFrame] | None = None,
    read_cached: Callable[[int], pd.DataFrame | None] | None = None,
    write_cached: Callable[[int, pd.DataFrame], None] | None = None,
) -> dict[str, dict[date, float]]:
    """Build full exchange-rate maps from minimum year to current year."""
    current_year = datetime.now().year
    load_year = year_loader or _fetch_exchange_rates_for_year
    read_cache = read_cached or read_cached_year_dataframe
    write_cache = write_cached or write_cached_year_dataframe
    yearly_tables = [
        _load_year_dataframe(
            year,
            current_year,
            load_year,
            read_cache,
            write_cache,
        )
        for year in range(min_year, current_year + 1)
    ]
    if not yearly_tables:
        return {"USD": {}, "EUR": {}}
    exchange_rates_df = pd.concat(yearly_tables).sort_index().shift()
    return {
        "USD": exchange_rates_df["_1USD"].to_dict(),
        "EUR": exchange_rates_df["_1EUR"].to_dict(),
    }


def _ensure_exchange_rates(min_year: int) -> dict[str, dict[date, float]]:
    """Return cached rates, refreshing cache when required."""
    current_year = datetime.now().year
    cached_rates = _CACHE_STATE.exchange_rates
    cached_min_year = _CACHE_STATE.min_year
    cached_current_year = _CACHE_STATE.current_year
    should_reload = (
        cached_rates is None
        or cached_min_year is None
        or cached_current_year != current_year
        or min_year < int(cached_min_year)
    )
    if should_reload:
        load_from_year = (
            min_year if cached_min_year is None else min(min_year, int(cached_min_year))
        )
        _CACHE_STATE.exchange_rates = _fetch_exchange_rates(load_from_year)
        _CACHE_STATE.min_year = load_from_year
        _CACHE_STATE.current_year = current_year
    if _CACHE_STATE.exchange_rates is None:
        raise ValueError("Exchange-rate cache is unexpectedly empty.")
    return _CACHE_STATE.exchange_rates


def get_exchange_rate(currency: str, date_: date) -> float:
    """Return PLN exchange rate for currency and date."""
    exchange_rates = _ensure_exchange_rates(date_.year)
    exchange_rates_currency = exchange_rates[currency]
    if date_ in exchange_rates_currency:
        return exchange_rates_currency[date_]
    previous_dates = [x for x in exchange_rates_currency if x < date_]
    if not previous_dates:
        raise ValueError(f"No exchange rate available for {currency} before {date_}.")
    return exchange_rates_currency[max(previous_dates)]


def load_and_concat_csv_files(csv_files: tuple[BytesIO, ...]) -> pd.DataFrame:
    """Load and concatenate CSV byte buffers into one dataframe."""
    return pd.concat([pd.read_csv(csv_file) for csv_file in csv_files], ignore_index=True)
