"""Tests for exchange-rate cache read/write helpers."""

import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from conftest import build_year_df
from pandas.testing import assert_frame_equal

from src import caches


class TestCaches(TestCase):
    """Test exchange-rate cache persistence behavior."""

    def test_read_and_write_cached_year_dataframe_roundtrip(self) -> None:
        """Test cached dataframe is read exactly as written."""
        year = 2024
        expected = build_year_df(year, usd=4.2, eur=4.7)
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {caches.CACHE_DIR_ENV_VAR: tmp_dir},
                clear=False,
            ):
                caches.write_cached_year_dataframe(year, expected)
                actual = caches.read_cached_year_dataframe(year)
        self.assertIsNotNone(actual)
        assert actual is not None
        assert_frame_equal(actual, expected.rename_axis(index=None))

    def test_cache_dir_falls_back_to_home_when_env_not_set(self) -> None:
        """Test default cache directory path when env override is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(caches.Path, "home", return_value=caches.Path("/tmp/home")):
                cache_dir = getattr(caches, "_cache_dir")()
        self.assertEqual(cache_dir, caches.Path("/tmp/home/.cache/polish-pit-calculator"))

    def test_read_cached_year_dataframe_returns_none_on_parse_error(self) -> None:
        """Test read returns None when cached CSV is malformed."""
        with patch.object(caches.pd, "read_csv", side_effect=caches.pd.errors.ParserError("x")):
            df = caches.read_cached_year_dataframe(2024)
        self.assertIsNone(df)

    def test_cache_dir_uses_env_override_when_set(self) -> None:
        """Test cache dir resolves from environment override value."""
        with patch.dict(
            os.environ,
            {caches.CACHE_DIR_ENV_VAR: "~/custom-cache"},
            clear=False,
        ):
            cache_dir = getattr(caches, "_cache_dir")()
        self.assertEqual(cache_dir, caches.Path("~/custom-cache").expanduser())

    def test_exchange_rates_cache_dir_appends_subdirectory(self) -> None:
        """Test exchange-rates cache directory is derived from root cache path."""
        with patch.object(caches, "_cache_dir", return_value=caches.Path("/tmp/cache-root")):
            rates_dir = getattr(caches, "_exchange_rates_cache_dir")()
        self.assertEqual(rates_dir, caches.Path("/tmp/cache-root/exchange-rates"))
