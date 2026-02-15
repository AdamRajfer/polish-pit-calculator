"""Tests for exchange-rate utility functions."""

from datetime import date, datetime
from typing import Any
from unittest import TestCase
from unittest.mock import Mock, call, patch

import pandas as pd
from conftest import build_year_df
from pandas.testing import assert_frame_equal

from src import utils as rates


def _private(name: str) -> Any:
    """Return a private utility callable by name."""
    return getattr(rates, name)


class TestExchangeRates(TestCase):
    """Test exchange-rate utility behavior and caching semantics."""

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

    def test_load_year_dataframe_skips_cache_for_current_year(self) -> None:
        """Test current-year data bypasses read/write cache helpers."""
        current_year = datetime.now().year
        fetched_df = build_year_df(current_year, usd=4.6, eur=5.1)
        year_loader = Mock(return_value=fetched_df)
        read_cached = Mock(return_value=build_year_df(current_year))
        write_cached = Mock()
        result = _private("_load_year_dataframe")(
            current_year,
            current_year,
            year_loader,
            read_cached,
            write_cached,
        )
        read_cached.assert_not_called()
        write_cached.assert_not_called()
        year_loader.assert_called_once_with(current_year)
        assert_frame_equal(result, fetched_df)

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

    def test_fetch_exchange_rates_returns_empty_maps_for_future_min_year(self) -> None:
        """Test empty table map when requested minimum year is in the future."""
        with patch("src.utils.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2025, 1, 1)
            loader = Mock()
            result = _private("_fetch_exchange_rates")(2026, year_loader=loader)
        self.assertEqual(result, {"USD": {}, "EUR": {}})
        loader.assert_not_called()

    def test_ensure_exchange_rates_raises_when_fetch_returns_none(self) -> None:
        """Test defensive error when cache remains empty after reload."""
        rates._CACHE_STATE.exchange_rates = None  # pylint: disable=protected-access
        rates._CACHE_STATE.min_year = None  # pylint: disable=protected-access
        rates._CACHE_STATE.current_year = None  # pylint: disable=protected-access
        with patch.object(rates, "_fetch_exchange_rates", return_value=None):
            with self.assertRaisesRegex(ValueError, "unexpectedly empty"):
                _private("_ensure_exchange_rates")(2024)

    def test_ensure_exchange_rates_reuses_cached_state(self) -> None:
        """Test cached rates are returned without reload when still valid."""
        current_year = datetime.now().year
        cached = {"USD": {date(2025, 1, 1): 4.0}, "EUR": {}}
        rates._CACHE_STATE.exchange_rates = cached  # pylint: disable=protected-access
        rates._CACHE_STATE.min_year = 2025  # pylint: disable=protected-access
        rates._CACHE_STATE.current_year = current_year  # pylint: disable=protected-access
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
