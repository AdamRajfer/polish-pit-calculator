"""Tests for cache and registry helpers."""

# pylint: disable=protected-access

import os
import stat
from datetime import date, datetime
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest import TestCase
from unittest.mock import call, patch
from urllib.error import HTTPError

import pandas as pd
from conftest import build_year_df
from pandas.testing import assert_frame_equal

import polish_pit_calculator.caches as caches_module
from polish_pit_calculator.caches import ExchangeRatesCache
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters import (
    CoinbaseTaxReporter,
    EmploymentTaxReporter,
    FileTaxReporter,
)


def _http_error(code: int, message: str) -> HTTPError:
    """Build typed HTTPError instance for tests."""
    headers = Message()
    return HTTPError("https://api.nbp.pl", code, message, headers, None)


class TestExchangeRatesCacheHelpers(TestCase):
    """Test helper methods used by exchange-rate cache implementation."""

    def setUp(self) -> None:
        ExchangeRatesCache.exchange_rates = None
        ExchangeRatesCache.min_year = None
        ExchangeRatesCache.current_year = None

    def test_cache_dir_uses_env_override_when_set(self) -> None:
        """Test cache dir resolves from environment override value."""
        with patch.dict(
            os.environ,
            {ExchangeRatesCache._dir_env_var_name: "~/custom-cache"},
            clear=False,
        ):
            cache_dir = ExchangeRatesCache.cache_dir()
        self.assertEqual(cache_dir, Path("~/custom-cache").expanduser())

    def test_cache_dir_falls_back_to_home_when_env_not_set(self) -> None:
        """Test default cache directory path when env override is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(Path, "home", return_value=Path("/tmp/home")):
                cache_dir = ExchangeRatesCache.cache_dir()
        self.assertEqual(cache_dir, Path("/tmp/home/.cache/polish-pit-calculator"))

    def test_read_and_write_cached_year_dataframe_roundtrip(self) -> None:
        """Test cached dataframe is read exactly as written."""
        year = 2024
        expected = build_year_df(year, usd=4.2, eur=4.7)
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {ExchangeRatesCache._dir_env_var_name: tmp_dir},
                clear=False,
            ):
                ExchangeRatesCache._write_cached_year_dataframe(year, expected)
                actual = ExchangeRatesCache._read_cached_year_dataframe(year)
        self.assertIsNotNone(actual)
        assert actual is not None
        assert_frame_equal(actual, expected.rename_axis(index=None))

    def test_read_cached_year_dataframe_returns_none_on_parse_error(self) -> None:
        """Test read returns None when cached CSV is malformed."""
        with patch.object(
            caches_module.pd,
            "read_csv",
            side_effect=caches_module.pd.errors.ParserError("x"),
        ):
            df = ExchangeRatesCache._read_cached_year_dataframe(2024)
        self.assertIsNone(df)

    def test_try_to_cast_string_to_float_handles_invalid_values(self) -> None:
        """Test helper returns None when value is invalid or missing comma."""
        cast_value = ExchangeRatesCache._try_to_cast_string_to_float
        self.assertEqual(cast_value("4,21"), 4.21)
        self.assertIsNone(cast_value("4.21"))
        self.assertIsNone(cast_value(None))

    def test_fetch_exchange_rates_for_year_parses_and_normalizes(self) -> None:
        """Test yearly fetch parses comma-decimals and normalizes columns."""
        raw = pd.DataFrame(
            {
                "data": ["2025-01-02", "2025-01-03"],
                "1USD": ["4,00", "4,10"],
                "1EUR": ["4,50", "4,60"],
                "ignore": ["x", "y"],
            }
        )
        with patch.object(caches_module.pd, "read_csv", return_value=raw) as read_csv:
            actual = ExchangeRatesCache._fetch_exchange_rates_for_year(2025)

        read_csv.assert_called_once()
        expected = pd.DataFrame(
            {"_1USD": [4.0, 4.1], "_1EUR": [4.5, 4.6]},
            index=[date(2025, 1, 2), date(2025, 1, 3)],
        )
        assert_frame_equal(actual, expected)

    def test_fetch_exchange_rates_for_date_range_parses_and_chunks(self) -> None:
        """Test date-range fetch chunks requests and parses USD/EUR rows."""
        start_date = date(2025, 1, 1)
        end_date = date(2025, 4, 5)
        first_chunk = pd.DataFrame(
            [
                {
                    "effectiveDate": "2025-01-02",
                    "rates": [{"code": "USD", "mid": 4.0}, {"code": "EUR", "mid": 4.5}],
                }
            ]
        )
        second_chunk = pd.DataFrame(
            [
                {
                    "effectiveDate": "2025-04-04",
                    "rates": [{"code": "USD", "mid": 4.2}, {"code": "EUR", "mid": 4.7}],
                },
                {"effectiveDate": "2025-04-05", "rates": [{"code": "GBP", "mid": 5.0}]},
            ]
        )
        with patch.object(
            caches_module.pd,
            "read_json",
            side_effect=[first_chunk, second_chunk],
        ) as read:
            actual = ExchangeRatesCache._fetch_exchange_rates_for_date_range(start_date, end_date)

        self.assertEqual(
            read.call_args_list,
            [
                call(
                    "https://api.nbp.pl/api/exchangerates/tables/A/"
                    "2025-01-01/2025-04-03/?format=json"
                ),
                call(
                    "https://api.nbp.pl/api/exchangerates/tables/A/"
                    "2025-04-04/2025-04-05/?format=json"
                ),
            ],
        )
        expected = pd.DataFrame(
            {"_1USD": [4.0, 4.2], "_1EUR": [4.5, 4.7]},
            index=[date(2025, 1, 2), date(2025, 4, 4)],
        ).rename_axis(index="Date")
        assert_frame_equal(actual, expected)

    def test_fetch_exchange_rates_for_date_range_adds_missing_currency_column(self) -> None:
        """Test date-range fetch inserts missing USD/EUR columns with NaN values."""
        with patch.object(
            caches_module.pd,
            "read_json",
            return_value=pd.DataFrame(
                [
                    {
                        "effectiveDate": "2025-01-02",
                        "rates": [{"code": "USD", "mid": 4.0}],
                    }
                ]
            ),
        ):
            actual = ExchangeRatesCache._fetch_exchange_rates_for_date_range(
                date(2025, 1, 2),
                date(2025, 1, 2),
            )

        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])
        self.assertTrue(pd.isna(actual.loc[date(2025, 1, 2), "_1EUR"]))

    def test_fetch_exchange_rates_for_date_range_returns_empty_when_input_invalid(self) -> None:
        """Test date-range fetch short-circuits when start date is after end date."""
        with patch.object(caches_module.pd, "read_json") as read:
            actual = ExchangeRatesCache._fetch_exchange_rates_for_date_range(
                date(2025, 1, 3),
                date(2025, 1, 2),
            )

        read.assert_not_called()
        self.assertTrue(actual.empty)
        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])

    def test_fetch_exchange_rates_for_date_range_skips_404_chunks(self) -> None:
        """Test date-range fetch treats 404 chunk responses as no new data."""
        not_found = _http_error(404, "not found")
        with patch.object(caches_module.pd, "read_json", side_effect=not_found) as read:
            actual = ExchangeRatesCache._fetch_exchange_rates_for_date_range(
                date(2025, 1, 4),
                date(2025, 1, 5),
            )

        read.assert_called_once()
        self.assertTrue(actual.empty)
        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])

    def test_fetch_exchange_rates_for_date_range_raises_non_404_http_error(self) -> None:
        """Test date-range fetch re-raises HTTP errors other than 404."""
        server_error = _http_error(500, "server error")
        with patch.object(caches_module.pd, "read_json", side_effect=server_error):
            with self.assertRaises(HTTPError):
                ExchangeRatesCache._fetch_exchange_rates_for_date_range(
                    date(2025, 1, 2),
                    date(2025, 1, 3),
                )


class TestExchangeRatesCacheLookup(TestCase):
    """Test get_exchange_rate behavior and cache-refresh flow."""

    def setUp(self) -> None:
        ExchangeRatesCache.exchange_rates = None
        ExchangeRatesCache.min_year = None
        ExchangeRatesCache.current_year = None

    def test_get_exchange_rate_uses_cached_state_without_reload(self) -> None:
        """When state is valid, get_exchange_rate should not reload exchange rates."""
        current_year = datetime.now().year
        query_date = date(current_year, 1, 3)
        ExchangeRatesCache.exchange_rates = {"USD": {query_date: 4.0}, "EUR": {}}
        ExchangeRatesCache.min_year = current_year
        ExchangeRatesCache.current_year = current_year

        with patch.object(ExchangeRatesCache, "_fetch_exchange_rates_for_year") as fetch_year:
            value = ExchangeRatesCache.get_exchange_rate("USD", query_date)

        self.assertEqual(value, 4.0)
        fetch_year.assert_not_called()

    def test_get_exchange_rate_uses_previous_available_day(self) -> None:
        """Previous available date should be used when exact date is missing."""
        current_year = datetime.now().year
        ExchangeRatesCache.exchange_rates = {"USD": {date(current_year, 1, 2): 4.1}, "EUR": {}}
        ExchangeRatesCache.min_year = current_year
        ExchangeRatesCache.current_year = current_year

        value = ExchangeRatesCache.get_exchange_rate("USD", date(current_year, 1, 3))
        self.assertEqual(value, 4.1)

    def test_get_exchange_rate_raises_when_no_previous_rate_exists(self) -> None:
        """Lookup should fail if no exact or previous date rate is available."""
        current_year = datetime.now().year
        ExchangeRatesCache.exchange_rates = {"USD": {date(current_year, 1, 2): 4.1}, "EUR": {}}
        ExchangeRatesCache.min_year = current_year
        ExchangeRatesCache.current_year = current_year

        with self.assertRaisesRegex(ValueError, "No exchange rate available"):
            ExchangeRatesCache.get_exchange_rate("USD", date(current_year, 1, 1))

    def test_get_exchange_rate_fetches_year_and_writes_when_cache_missing(self) -> None:
        """Missing yearly cache should trigger yearly fetch and write."""
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 3)
            with patch.object(ExchangeRatesCache, "_read_cached_year_dataframe", return_value=None):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_year",
                    return_value=build_year_df(2025, usd=4.6, eur=5.1),
                ) as fetch_year:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2025, 1, 3))

        self.assertEqual(value, 4.6)
        fetch_year.assert_called_once_with(2025)
        write_year.assert_called_once()

    def test_get_exchange_rate_reloads_with_previous_year_cached_data(self) -> None:
        """Previous-year loop branch should reuse cached table and skip yearly fetch."""
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 3)
            with patch.object(
                ExchangeRatesCache,
                "_read_cached_year_dataframe",
                side_effect=[build_year_df(2024, usd=4.8, eur=5.2), build_year_df(2025)],
            ):
                with patch.object(
                    ExchangeRatesCache, "_fetch_exchange_rates_for_year"
                ) as fetch_year:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2024, 1, 3))

        self.assertEqual(value, 4.8)
        fetch_year.assert_not_called()
        write_year.assert_not_called()

    def test_get_exchange_rate_reloads_with_previous_year_cache_miss(self) -> None:
        """Previous-year cache miss should fetch and persist that year."""
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 3)
            with patch.object(
                ExchangeRatesCache,
                "_read_cached_year_dataframe",
                side_effect=[None, build_year_df(2025)],
            ):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_year",
                    return_value=build_year_df(2024, usd=5.0, eur=5.5),
                ) as fetch_year:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2024, 1, 3))

        self.assertEqual(value, 5.0)
        fetch_year.assert_called_once_with(2024)
        write_year.assert_called_once()

    def test_get_exchange_rate_current_year_refreshes_missing_range(self) -> None:
        """Stale current-year cache should fetch missing date range and persist merged table."""
        cached_df = build_year_df(2025).iloc[0:1]
        missing_df = pd.DataFrame(
            {"_1USD": [4.2], "_1EUR": [4.7]},
            index=[date(2025, 1, 3)],
        ).rename_axis(index="Date")

        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 4)
            with patch.object(
                ExchangeRatesCache, "_read_cached_year_dataframe", return_value=cached_df
            ):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_date_range",
                    return_value=missing_df,
                ) as fetch_range:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2025, 1, 4))

        self.assertEqual(value, 4.0)
        fetch_range.assert_called_once_with(date(2025, 1, 3), date(2025, 1, 4))
        write_year.assert_called_once()

    def test_get_exchange_rate_current_year_cache_up_to_date_skips_refresh(self) -> None:
        """Current-year cache newer than today should not call date-range fetch."""
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 3)
            with patch.object(
                ExchangeRatesCache,
                "_read_cached_year_dataframe",
                return_value=build_year_df(2025),
            ):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_date_range",
                ) as fetch_range:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2025, 1, 3))

        self.assertEqual(value, 4.0)
        fetch_range.assert_not_called()
        write_year.assert_not_called()

    def test_get_exchange_rate_current_year_keeps_cache_when_refresh_empty(self) -> None:
        """Empty incremental fetch should keep cache without rewrite."""
        cached_df = build_year_df(2025)
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 4)
            with patch.object(
                ExchangeRatesCache, "_read_cached_year_dataframe", return_value=cached_df
            ):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_date_range",
                    return_value=pd.DataFrame(columns=["_1USD", "_1EUR"]).rename_axis(index="Date"),
                ) as fetch_range:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2025, 1, 4))

        self.assertEqual(value, 4.0)
        fetch_range.assert_called_once_with(date(2025, 1, 4), date(2025, 1, 4))
        write_year.assert_not_called()

    def test_get_exchange_rate_current_year_keeps_cache_on_refresh_error(self) -> None:
        """Date-range fetch errors should keep cache without rewrite."""
        cached_df = build_year_df(2025)
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 4)
            with patch.object(
                ExchangeRatesCache, "_read_cached_year_dataframe", return_value=cached_df
            ):
                with patch.object(
                    ExchangeRatesCache,
                    "_fetch_exchange_rates_for_date_range",
                    side_effect=OSError("network issue"),
                ) as fetch_range:
                    with patch.object(
                        ExchangeRatesCache,
                        "_write_cached_year_dataframe",
                    ) as write_year:
                        value = ExchangeRatesCache.get_exchange_rate("USD", date(2025, 1, 4))

        self.assertEqual(value, 4.0)
        fetch_range.assert_called_once_with(date(2025, 1, 4), date(2025, 1, 4))
        write_year.assert_not_called()

    def test_get_exchange_rate_future_year_builds_empty_map_and_raises(self) -> None:
        """Future min-year request should produce empty map and fail for missing rate."""
        with patch("polish_pit_calculator.caches.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 1)
            with self.assertRaisesRegex(ValueError, "No exchange rate available"):
                ExchangeRatesCache.get_exchange_rate("USD", date(2026, 1, 1))
        self.assertEqual(ExchangeRatesCache.exchange_rates, {"USD": {}, "EUR": {}})


class TestRegistryHelpers(TestCase):
    """Test on-disk registry helpers in TaxReporterRegistry."""

    def test_registry_dir_uses_env_override_when_set(self) -> None:
        """Test registry directory resolves from dedicated env override."""
        with patch.dict(
            os.environ,
            {TaxReporterRegistry._dir_env_var_name: "~/pit-registry"},
            clear=False,
        ):
            registry_dir = TaxReporterRegistry.registry_dir()
        self.assertEqual(registry_dir, Path("~/pit-registry").expanduser())

    def test_registry_dir_falls_back_to_hidden_home_path(self) -> None:
        """Test registry directory defaults to ~/.polish-pit-calculator."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(Path, "home", return_value=Path("/tmp/home")):
                registry_dir = TaxReporterRegistry.registry_dir()
        self.assertEqual(registry_dir, Path("/tmp/home/.polish-pit-calculator"))

    def test_serialize_creates_base_and_registry_dirs(self) -> None:
        """Test reporter serialization bootstraps registry directory with private mode."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: tmp_dir},
                clear=False,
            ):
                reporter = CoinbaseTaxReporter("/tmp/source.csv")
                entry_id = TaxReporterRegistry.serialize(reporter)
                path = TaxReporterRegistry.registry_dir() / f"{entry_id}.yaml"
                registry = Path(tmp_dir)
                self.assertEqual(path.parent, registry)
                self.assertEqual(path.suffix, ".yaml")
                self.assertTrue(path.stem.isdigit())
                self.assertEqual(len(path.stem), 9)
                self.assertTrue(registry.is_dir())
                self.assertEqual(stat.S_IMODE(registry.stat().st_mode), 0o700)

    def test_serialize_and_read_registry_entries_roundtrip(self) -> None:
        """Test serialization writes decodable entries readable through registry helpers."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: tmp_dir},
                clear=False,
            ):
                entry_id = TaxReporterRegistry.serialize(CoinbaseTaxReporter("/tmp/source.csv"))
                path = TaxReporterRegistry.registry_dir() / f"{entry_id}.yaml"
                entries = TaxReporterRegistry.deserialize_all()
            self.assertTrue(path.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0][0], entry_id)
            reporter = entries[0][1]
            self.assertIsInstance(reporter, CoinbaseTaxReporter)
            self.assertEqual(str(cast(FileTaxReporter, reporter).path), "/tmp/source.csv")

    def test_read_registry_entries_filters_by_reporter_class(self) -> None:
        """Test optional reporter_cls filter keeps only matching registry entries."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: tmp_dir},
                clear=False,
            ):
                TaxReporterRegistry.serialize(CoinbaseTaxReporter("/tmp/raw.csv"))
                TaxReporterRegistry.serialize(EmploymentTaxReporter(2025, 1.0, 2.0, 3.0, 4.0))
                entries = TaxReporterRegistry.deserialize_all(CoinbaseTaxReporter)
            self.assertEqual(len(entries), 1)
            reporter = entries[0][1]
            self.assertIsInstance(reporter, CoinbaseTaxReporter)
            self.assertEqual(str(cast(FileTaxReporter, reporter).path), "/tmp/raw.csv")

    def test_read_registry_entries_returns_empty_for_missing_dir(self) -> None:
        """Test registry-entry listing returns empty list when directory does not exist."""
        with TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing-registry"
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: str(missing)},
                clear=False,
            ):
                self.assertEqual(TaxReporterRegistry.deserialize_all(), [])

    def test_deserialize_raises_on_read_error(self) -> None:
        """Test TaxReporterRegistry.deserialize raises when file IO fails."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: tmp_dir},
                clear=False,
            ):
                registry = Path(tmp_dir)
                registry.mkdir(parents=True, exist_ok=True)
                (registry / "000000001.yaml").write_text("x", encoding="utf-8")
                with patch.object(Path, "read_text", side_effect=OSError("boom")):
                    with self.assertRaises(OSError):
                        TaxReporterRegistry.deserialize("000000001")

    def test_deserialize_raises_when_registry_directory_missing(self) -> None:
        """Test TaxReporterRegistry.deserialize raises when registry directory is missing."""
        with TemporaryDirectory() as tmp_dir:
            missing = Path(tmp_dir) / "missing-registry"
            with patch.dict(
                os.environ,
                {TaxReporterRegistry._dir_env_var_name: str(missing)},
                clear=False,
            ):
                with self.assertRaises(FileNotFoundError):
                    TaxReporterRegistry.deserialize("000000001")
