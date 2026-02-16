"""Tests for exchange-rate cache read/write helpers."""

import os
import stat
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

    def test_registry_dir_uses_env_override_when_set(self) -> None:
        """Test registry directory resolves from dedicated env override."""
        with patch.dict(
            os.environ,
            {caches.REGISTRY_DIR_ENV_VAR: "~/pit-registry"},
            clear=False,
        ):
            registry_dir = getattr(caches, "_registry_dir")()
        self.assertEqual(registry_dir, caches.Path("~/pit-registry").expanduser())

    def test_registry_dir_falls_back_to_hidden_home_path(self) -> None:
        """Test registry directory defaults to ~/.polish-pit-calculator."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(caches.Path, "home", return_value=caches.Path("/tmp/home")):
                registry_dir = getattr(caches, "_registry_dir")()
        self.assertEqual(registry_dir, caches.Path("/tmp/home/.polish-pit-calculator"))

    def test_write_registry_entry_creates_base_and_registry_dirs(self) -> None:
        """Test writing registry entry bootstraps both directories with private mode."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: tmp_dir},
                clear=False,
            ):
                path = caches.write_registry_entry(
                    1,
                    {"tax_report_key": "raw_custom_csv", "path": "/tmp/source.csv"},
                )
                registry = caches.Path(tmp_dir)
                self.assertEqual(path, registry / "1.yaml")
                self.assertTrue(registry.is_dir())
                self.assertEqual(stat.S_IMODE(registry.stat().st_mode), 0o700)

    def test_write_and_read_registry_entries_roundtrip(self) -> None:
        """Test registry entry helpers encode/write and decode/read payloads."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: tmp_dir},
                clear=False,
            ):
                path = caches.write_registry_entry(
                    1,
                    {"tax_report_key": "raw_custom_csv", "path": "/tmp/source.csv"},
                )
                decoded_payload = caches.read_registry_entry(1)
            self.assertNotEqual(path.read_text(encoding="utf-8"), "path: /tmp/source.csv\n")
            self.assertEqual(
                decoded_payload, {"tax_report_key": "raw_custom_csv", "path": "/tmp/source.csv"}
            )

    def test_read_registry_entry_ids_returns_empty_for_missing_dir(self) -> None:
        """Test registry-id listing returns empty list when directory does not exist."""
        with TemporaryDirectory() as tmp_dir:
            missing = caches.Path(tmp_dir) / "missing-registry"
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: str(missing)},
                clear=False,
            ):
                self.assertEqual(caches.read_registry_entry_ids(), [])

    def test_read_registry_entry_ids_enforces_private_directory_mode(self) -> None:
        """Test registry-id read path chmods existing registry directory to 0700."""
        with TemporaryDirectory() as tmp_dir:
            registry = caches.Path(tmp_dir) / "registry"
            registry.mkdir(parents=True, exist_ok=True, mode=0o750)
            registry.chmod(0o750)
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: str(registry)},
                clear=False,
            ):
                self.assertEqual(caches.read_registry_entry_ids(), [])
            self.assertEqual(stat.S_IMODE(registry.stat().st_mode), 0o700)

    def test_read_registry_entry_raises_on_read_error(self) -> None:
        """Test registry entry read helper raises when file IO fails."""
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: tmp_dir},
                clear=False,
            ):
                registry = caches.Path(tmp_dir)
                registry.mkdir(parents=True, exist_ok=True)
                (registry / "1.yaml").write_text("x", encoding="utf-8")
                with patch.object(caches.Path, "read_text", side_effect=OSError("boom")):
                    with self.assertRaises(OSError):
                        caches.read_registry_entry(1)

    def test_read_registry_entry_raises_when_registry_directory_missing(self) -> None:
        """Test registry entry read raises when registry directory does not exist."""
        with TemporaryDirectory() as tmp_dir:
            missing = caches.Path(tmp_dir) / "missing-registry"
            with patch.dict(
                os.environ,
                {caches.REGISTRY_DIR_ENV_VAR: str(missing)},
                clear=False,
            ):
                with self.assertRaises(FileNotFoundError):
                    caches.read_registry_entry(1)
