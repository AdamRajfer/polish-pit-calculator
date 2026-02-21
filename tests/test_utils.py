"""Tests for exchange-rate utility functions."""

from datetime import date, datetime
from email.message import Message
from typing import Any
from unittest import TestCase
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError

import pandas as pd
from conftest import build_year_df
from pandas.testing import assert_frame_equal

from src import utils as rates


def _private(name: str) -> Any:
    """Return a private utility callable by name."""
    return getattr(rates, name)


def _http_error(code: int, message: str) -> HTTPError:
    """Build typed HTTPError instance for tests."""
    headers = Message()
    return HTTPError("https://api.nbp.pl", code, message, headers, None)


class TestLoadYearDataframe(TestCase):
    """Test yearly exchange-rate loading and on-disk cache behavior."""

    def test_load_year_dataframe_uses_cache_for_previous_year(self) -> None:
        """Test previous-year data uses cache when present."""
        current_year = datetime.now().year
        previous_year = current_year - 1
        cached_df = build_year_df(previous_year)
        year_loader = Mock()
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        result = _private("_load_year_dataframe")(
            previous_year,
            current_year,
            year_loader,
            read_cached,
            write_cached,
        )

        read_cached.assert_called_once_with(previous_year)
        year_loader.assert_not_called()
        write_cached.assert_not_called()
        assert_frame_equal(result, cached_df)

    def test_load_year_dataframe_writes_cache_for_previous_year(self) -> None:
        """Test fetched previous-year data is written to cache."""
        current_year = datetime.now().year
        previous_year = current_year - 1
        fetched_df = build_year_df(previous_year, usd=4.3, eur=4.8)
        year_loader = Mock(return_value=fetched_df)
        read_cached = Mock(return_value=None)
        write_cached = Mock()

        result = _private("_load_year_dataframe")(
            previous_year,
            current_year,
            year_loader,
            read_cached,
            write_cached,
        )

        read_cached.assert_called_once_with(previous_year)
        year_loader.assert_called_once_with(previous_year)
        write_cached.assert_called_once_with(previous_year, fetched_df)
        assert_frame_equal(result, fetched_df)

    def test_load_year_dataframe_writes_cache_for_current_year_when_missing(self) -> None:
        """Test current-year cache miss fetches yearly file and persists it."""
        current_year = datetime.now().year
        fetched_df = build_year_df(current_year, usd=4.6, eur=5.1)
        year_loader = Mock(return_value=fetched_df)
        read_cached = Mock(return_value=None)
        write_cached = Mock()

        result = _private("_load_year_dataframe")(
            current_year,
            current_year,
            year_loader,
            read_cached,
            write_cached,
        )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_called_once_with(current_year)
        write_cached.assert_called_once_with(current_year, fetched_df)
        assert_frame_equal(result, fetched_df)

    def test_load_year_dataframe_writes_cache_for_current_year_when_cached_df_empty(self) -> None:
        """Test empty current-year cache is treated as cache miss."""
        current_year = datetime.now().year
        cached_df = build_year_df(current_year).iloc[0:0]
        fetched_df = build_year_df(current_year, usd=4.6, eur=5.1)
        year_loader = Mock(return_value=fetched_df)
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        result = _private("_load_year_dataframe")(
            current_year,
            current_year,
            year_loader,
            read_cached,
            write_cached,
        )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_called_once_with(current_year)
        write_cached.assert_called_once_with(current_year, fetched_df)
        assert_frame_equal(result, fetched_df)

    def test_load_year_dataframe_reuses_current_year_cache_when_up_to_date(self) -> None:
        """Test current-year cache is reused when it already covers today's date."""
        current_year = datetime.now().year
        cached_df = build_year_df(current_year, usd=4.6, eur=5.1)
        year_loader = Mock()
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(current_year, 1, 3)
            with patch.object(rates, "_fetch_exchange_rates_for_date_range") as range_loader:
                result = _private("_load_year_dataframe")(
                    current_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_not_called()
        range_loader.assert_not_called()
        write_cached.assert_not_called()
        assert_frame_equal(result, cached_df)

    def test_load_year_dataframe_merges_missing_current_year_range(self) -> None:
        """Test current-year cache refresh appends missing dates and rewrites cache."""
        current_year = datetime.now().year
        cached_df = build_year_df(current_year).iloc[0:1]
        missing_df = pd.DataFrame(
            {"_1USD": [4.2], "_1EUR": [4.7]},
            index=[date(current_year, 1, 3)],
        ).rename_axis(index="Date")
        year_loader = Mock()
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(current_year, 1, 4)
            with patch.object(
                rates,
                "_fetch_exchange_rates_for_date_range",
                return_value=missing_df,
            ) as range_loader:
                result = _private("_load_year_dataframe")(
                    current_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_not_called()
        range_loader.assert_called_once_with(
            date(current_year, 1, 3),
            date(current_year, 1, 4),
        )
        expected = pd.concat([cached_df, missing_df]).sort_index().rename_axis(index="Date")
        write_cached.assert_called_once()
        written_year, written_df = write_cached.call_args.args
        self.assertEqual(written_year, current_year)
        assert_frame_equal(written_df, expected)
        assert_frame_equal(result, expected)

    def test_load_year_dataframe_keeps_current_year_cache_when_range_fetch_empty(self) -> None:
        """Test current-year cache stays unchanged when no new rows are returned."""
        current_year = datetime.now().year
        cached_df = build_year_df(current_year).iloc[0:1]
        year_loader = Mock()
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(current_year, 1, 4)
            with patch.object(
                rates,
                "_fetch_exchange_rates_for_date_range",
                return_value=pd.DataFrame(columns=["_1USD", "_1EUR"]).rename_axis(index="Date"),
            ) as range_loader:
                result = _private("_load_year_dataframe")(
                    current_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_not_called()
        range_loader.assert_called_once_with(date(current_year, 1, 3), date(current_year, 1, 4))
        write_cached.assert_not_called()
        assert_frame_equal(result, cached_df)

    def test_load_year_dataframe_keeps_current_year_cache_on_range_fetch_error(self) -> None:
        """Test current-year cache is reused when incremental fetch raises error."""
        current_year = datetime.now().year
        cached_df = build_year_df(current_year).iloc[0:1]
        year_loader = Mock()
        read_cached = Mock(return_value=cached_df)
        write_cached = Mock()

        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(current_year, 1, 4)
            with patch.object(
                rates,
                "_fetch_exchange_rates_for_date_range",
                side_effect=OSError("network issue"),
            ) as range_loader:
                result = _private("_load_year_dataframe")(
                    current_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                )

        read_cached.assert_called_once_with(current_year)
        year_loader.assert_not_called()
        range_loader.assert_called_once_with(date(current_year, 1, 3), date(current_year, 1, 4))
        write_cached.assert_not_called()
        assert_frame_equal(result, cached_df)


class TestExchangeRateFetchers(TestCase):
    """Test exchange-rate parsing, fetching and merge helpers."""

    def test_fetch_exchange_rates_uses_requested_range(self) -> None:
        """Test requested year range is loaded in correct order."""
        current_year = datetime.now().year
        start_year = current_year - 1
        year_loader = Mock()
        read_cached = Mock()
        write_cached = Mock()

        with patch.object(rates, "_load_year_dataframe") as load:
            load.side_effect = [
                build_year_df(start_year, usd=4.2, eur=4.7),
                build_year_df(current_year, usd=4.4, eur=4.9),
            ]
            exchange_rates = _private("_fetch_exchange_rates")(
                start_year,
                year_loader=year_loader,
                read_cached=read_cached,
                write_cached=write_cached,
            )

        self.assertEqual(
            load.call_args_list,
            [
                call(
                    start_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                ),
                call(
                    current_year,
                    current_year,
                    year_loader,
                    read_cached,
                    write_cached,
                ),
            ],
        )
        yearly = [
            build_year_df(start_year, usd=4.2, eur=4.7),
            build_year_df(current_year, usd=4.4, eur=4.9),
        ]
        rates_df = pd.concat(yearly).sort_index().shift()
        actual = pd.DataFrame(exchange_rates).sort_index()
        expected = (
            pd.DataFrame({"USD": rates_df["_1USD"], "EUR": rates_df["_1EUR"]})
            .sort_index()
            .rename_axis(index=None)
        )
        assert_frame_equal(actual, expected)

    def test_try_to_cast_string_to_float_handles_invalid_values(self) -> None:
        """Test helper returns None when value is invalid or missing comma."""
        cast_value = _private("_try_to_cast_string_to_float")
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
        with patch.object(rates.pd, "read_csv", return_value=raw) as read_csv:
            actual = _private("_fetch_exchange_rates_for_year")(2025)
        read_csv.assert_called_once()
        expected = pd.DataFrame(
            {
                "_1USD": [4.0, 4.1],
                "_1EUR": [4.5, 4.6],
            },
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
                    "rates": [
                        {"code": "USD", "mid": 4.0},
                        {"code": "EUR", "mid": 4.5},
                    ],
                }
            ]
        )
        second_chunk = pd.DataFrame(
            [
                {
                    "effectiveDate": "2025-04-04",
                    "rates": [
                        {"code": "USD", "mid": 4.2},
                        {"code": "EUR", "mid": 4.7},
                    ],
                },
                {
                    "effectiveDate": "2025-04-05",
                    "rates": [{"code": "GBP", "mid": 5.0}],
                },
            ]
        )
        with patch.object(rates.pd, "read_json", side_effect=[first_chunk, second_chunk]) as read:
            actual = _private("_fetch_exchange_rates_for_date_range")(start_date, end_date)

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
            rates.pd,
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
            actual = _private("_fetch_exchange_rates_for_date_range")(
                date(2025, 1, 2),
                date(2025, 1, 2),
            )

        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])
        self.assertTrue(pd.isna(actual.loc[date(2025, 1, 2), "_1EUR"]))

    def test_fetch_exchange_rates_for_date_range_returns_empty_when_input_invalid(self) -> None:
        """Test date-range fetch short-circuits when start date is after end date."""
        with patch.object(rates.pd, "read_json") as read:
            actual = _private("_fetch_exchange_rates_for_date_range")(
                date(2025, 1, 3),
                date(2025, 1, 2),
            )

        read.assert_not_called()
        self.assertTrue(actual.empty)
        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])

    def test_fetch_exchange_rates_for_date_range_skips_404_chunks(self) -> None:
        """Test date-range fetch treats 404 chunk responses as no new data."""
        not_found = _http_error(404, "not found")
        with patch.object(rates.pd, "read_json", side_effect=not_found) as read:
            actual = _private("_fetch_exchange_rates_for_date_range")(
                date(2025, 1, 4),
                date(2025, 1, 5),
            )

        read.assert_called_once()
        self.assertTrue(actual.empty)
        self.assertEqual(list(actual.columns), ["_1USD", "_1EUR"])

    def test_fetch_exchange_rates_for_date_range_raises_non_404_http_error(self) -> None:
        """Test date-range fetch re-raises HTTP errors other than 404."""
        server_error = _http_error(500, "server error")
        with patch.object(rates.pd, "read_json", side_effect=server_error):
            with self.assertRaises(HTTPError):
                _private("_fetch_exchange_rates_for_date_range")(
                    date(2025, 1, 2),
                    date(2025, 1, 3),
                )

    def test_merge_exchange_rates_dataframes_deduplicates_dates(self) -> None:
        """Test merge helper keeps latest row for duplicate dates."""
        cached = pd.DataFrame(
            {"_1USD": [4.0], "_1EUR": [4.5]},
            index=[date(2025, 1, 2)],
        ).rename_axis(index="Date")
        fetched = pd.DataFrame(
            {"_1USD": [4.1, 4.2], "_1EUR": [4.6, 4.7]},
            index=[date(2025, 1, 2), date(2025, 1, 3)],
        ).rename_axis(index="Date")

        actual = _private("_merge_exchange_rates_dataframes")(cached, fetched)

        expected = pd.DataFrame(
            {"_1USD": [4.1, 4.2], "_1EUR": [4.6, 4.7]},
            index=[date(2025, 1, 2), date(2025, 1, 3)],
        ).rename_axis(index="Date")
        assert_frame_equal(actual, expected)

    def test_fetch_exchange_rates_returns_empty_maps_for_future_min_year(self) -> None:
        """Test empty table map when requested minimum year is in the future."""
        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 1)
            loader = Mock()
            result = _private("_fetch_exchange_rates")(2026, year_loader=loader)

        self.assertEqual(result, {"USD": {}, "EUR": {}})
        loader.assert_not_called()


class TestExchangeRateLookupAndCacheState(TestCase):
    """Test exchange-rate lookup semantics and in-memory cache state handling."""

    def test_get_exchange_rate_loads_and_reuses_cached_rates(self) -> None:
        """Test direct-date lookup reads value from prepared map."""
        with patch.object(
            rates,
            "_ensure_exchange_rates",
            return_value={"USD": {date(2025, 1, 1): 4.0}},
        ) as ensure:
            first = rates.get_exchange_rate("USD", date(2025, 1, 1))
            second = rates.get_exchange_rate("USD", date(2025, 1, 1))

        self.assertEqual((first, second), (4.0, 4.0))
        self.assertEqual(ensure.call_args_list, [call(2025), call(2025)])

    def test_get_exchange_rate_uses_previous_available_day(self) -> None:
        """Test previous available date is used when exact date is missing."""
        with patch.object(
            rates,
            "_ensure_exchange_rates",
            return_value={"USD": {date(2025, 1, 2): 4.1}},
        ):
            value = rates.get_exchange_rate("USD", date(2025, 1, 3))

        self.assertEqual(value, 4.1)

    def test_ensure_exchange_rates_raises_when_fetch_returns_none(self) -> None:
        """Test defensive error when cache remains empty after reload."""
        cache_state = getattr(rates, "_CACHE_STATE")
        setattr(cache_state, "exchange_rates", None)
        setattr(cache_state, "min_year", None)
        setattr(cache_state, "current_year", None)

        with patch.object(rates, "_fetch_exchange_rates", return_value=None):
            with self.assertRaisesRegex(ValueError, "unexpectedly empty"):
                _private("_ensure_exchange_rates")(2024)

    def test_ensure_exchange_rates_reuses_cached_state(self) -> None:
        """Test cached rates are returned without reload when still valid."""
        current_year = datetime.now().year
        cached = {"USD": {date(2025, 1, 1): 4.0}, "EUR": {}}
        cache_state = getattr(rates, "_CACHE_STATE")
        setattr(cache_state, "exchange_rates", cached)
        setattr(cache_state, "min_year", 2025)
        setattr(cache_state, "current_year", current_year)

        with patch.object(rates, "_fetch_exchange_rates") as fetch:
            value = _private("_ensure_exchange_rates")(2025)

        self.assertIs(value, cached)
        fetch.assert_not_called()

    def test_get_exchange_rate_raises_when_no_previous_rate_exists(self) -> None:
        """Test lookup raises if no exact or previous date is available."""
        with patch.object(
            rates,
            "_ensure_exchange_rates",
            return_value={"USD": {date(2025, 1, 2): 4.1}},
        ):
            with self.assertRaisesRegex(ValueError, "No exchange rate available"):
                rates.get_exchange_rate("USD", date(2025, 1, 1))
