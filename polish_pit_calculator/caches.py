"""Exchange rate cache helpers."""

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd


class ExchangeRatesCache:
    """Singleton class for exchange-rate cache state and cache directory management."""

    _dir_env_var_name = "POLISH_PIT_CALCULATOR_CACHE_DIR"
    exchange_rates: dict[str, dict[date, float]] | None = None
    min_year: int | None = None
    current_year: int | None = None

    @classmethod
    def cache_dir(cls) -> Path:
        """Return base cache directory path for the application."""
        if path := os.environ.get(cls._dir_env_var_name):
            return Path(path).expanduser()
        return Path.home() / ".cache" / "polish-pit-calculator"

    @classmethod
    def get_exchange_rate(cls, currency: str, date_: date) -> float:
        """Return PLN exchange rate for currency and date."""
        current_year = datetime.now().year
        if cls._should_reload(date_, current_year):
            cls._reload_exchange_rates(date_, current_year)
        exchange_rates = (
            cls.exchange_rates if isinstance(cls.exchange_rates, dict) else {"USD": {}, "EUR": {}}
        )
        exchange_rates_currency = exchange_rates[currency]
        if date_ in exchange_rates_currency:
            return exchange_rates_currency[date_]
        previous_dates = [x for x in exchange_rates_currency if x < date_]
        if not previous_dates:
            raise ValueError(f"No exchange rate available for {currency} before {date_}.")
        return exchange_rates_currency[max(previous_dates)]

    @classmethod
    def _should_reload(cls, date_: date, current_year: int) -> bool:
        """Return whether in-memory exchange-rate cache must be refreshed."""
        return (
            cls.exchange_rates is None
            or cls.min_year is None
            or cls.current_year != current_year
            or date_.year < int(cls.min_year)
        )

    @classmethod
    def _reload_exchange_rates(cls, date_: date, current_year: int) -> None:
        """Reload full in-memory cache from persisted data and network fallbacks."""
        min_year = date_.year if cls.min_year is None else min(date_.year, int(cls.min_year))
        yearly_tables = [
            cls._load_year_dataframe(year, current_year)
            for year in range(min_year, current_year + 1)
        ]
        if yearly_tables:
            exchange_rates_df = pd.concat(yearly_tables).sort_index().shift()
            cls.exchange_rates = {
                "USD": exchange_rates_df["_1USD"].to_dict(),
                "EUR": exchange_rates_df["_1EUR"].to_dict(),
            }
        else:
            cls.exchange_rates = {"USD": {}, "EUR": {}}
        cls.min_year = min_year
        cls.current_year = current_year

    @classmethod
    def _load_year_dataframe(cls, year: int, current_year: int) -> pd.DataFrame:
        """Load one year's exchange rates from cache and remote source if needed."""
        cached = cls._read_cached_year_dataframe(year)
        if year < current_year:
            return cls._load_past_year_dataframe(year, cached)
        return cls._load_current_year_dataframe(year, cached)

    @classmethod
    def _load_past_year_dataframe(
        cls,
        year: int,
        cached: pd.DataFrame | None,
    ) -> pd.DataFrame:
        """Return historical year dataframe, persisting fetched data on cache misses."""
        if cached is not None:
            return cached
        previous_df = cls._fetch_exchange_rates_for_year(year)
        cls._write_cached_year_dataframe(year, previous_df)
        return previous_df

    @classmethod
    def _load_current_year_dataframe(
        cls,
        year: int,
        cached: pd.DataFrame | None,
    ) -> pd.DataFrame:
        """Return current year dataframe merged with latest available remote rows."""
        if cached is None or cached.empty:
            current_df = cls._fetch_exchange_rates_for_year(year)
            cls._write_cached_year_dataframe(year, current_df)
            return current_df

        current_date = datetime.now().date()
        latest_cached_date = max(cached.index)
        if latest_cached_date >= current_date:
            return cached

        missing_start = max(date(year, 1, 1), latest_cached_date + timedelta(days=1))
        try:
            missing_df = cls._fetch_exchange_rates_for_date_range(
                missing_start,
                current_date,
            )
        except (OSError, ValueError, pd.errors.ParserError):
            return cached
        if missing_df.empty:
            return cached

        merged = pd.concat([cached, missing_df]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.rename_axis(index="Date")
        cls._write_cached_year_dataframe(year, merged)
        return merged

    @classmethod
    def _read_cached_year_dataframe(cls, year: int) -> pd.DataFrame | None:
        """Read cached exchange rates table for a single year."""
        path = cls.cache_dir() / f"{year}.csv"
        try:
            df = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
        except (FileNotFoundError, OSError, ValueError, pd.errors.ParserError):
            return None
        df.index = df.index.date
        return df

    @classmethod
    def _write_cached_year_dataframe(cls, year: int, df: pd.DataFrame) -> None:
        """Persist exchange rates table for a single year."""
        path = cls.cache_dir() / f"{year}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index_label="Date")

    @staticmethod
    def _try_to_cast_string_to_float(value: object) -> float | None:
        """Convert NBP decimal string using comma separator to float."""
        try:
            assert "," in str(value)
            return float(str(value).replace(",", "."))
        except (ValueError, AssertionError, TypeError):
            return None

    @classmethod
    def _fetch_exchange_rates_for_year(cls, year: int) -> pd.DataFrame:
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
            .map(cls._try_to_cast_string_to_float)
            .dropna(axis=1, how="all")
            .dropna(axis=0, how="all")
            .astype(float)
            .rename_axis(index="Date")
        )
        df.index = pd.to_datetime(df.index).date
        df.columns = [f"_{x}" if x[0].isdigit() else x for x in df.columns]
        return df

    @staticmethod
    def _fetch_exchange_rates_for_date_range(start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch NBP table A rows for an inclusive date range."""
        if start_date > end_date:
            return pd.DataFrame(columns=["_1USD", "_1EUR"]).rename_axis(index="Date")

        rows: list[dict[str, date | float]] = []
        current_start = start_date
        while current_start <= end_date:
            current_end = min(current_start + timedelta(days=92), end_date)
            try:
                raw = pd.read_json(
                    (
                        "https://api.nbp.pl/api/exchangerates/tables/A/"
                        f"{current_start.isoformat()}/{current_end.isoformat()}/?format=json"
                    )
                )
            except HTTPError as error:
                if error.code == 404:
                    current_start = current_end + timedelta(days=1)
                    continue
                raise

            for row in raw.itertuples(index=False):
                rates = {
                    f"_1{rate['code']}": float(rate["mid"])
                    for rate in row.rates
                    if rate["code"] in {"USD", "EUR"}
                }
                if not rates:
                    continue
                rows.append({"Date": pd.Timestamp(row.effectiveDate).date(), **rates})
            current_start = current_end + timedelta(days=1)

        if not rows:
            return pd.DataFrame(columns=["_1USD", "_1EUR"]).rename_axis(index="Date")
        df = pd.DataFrame(rows).set_index("Date").sort_index().rename_axis(index="Date")
        for column in ("_1USD", "_1EUR"):
            if column not in df.columns:
                df[column] = float("nan")
        return df[["_1USD", "_1EUR"]].astype(float)
