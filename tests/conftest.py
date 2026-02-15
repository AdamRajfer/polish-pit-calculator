"""Shared pytest fixtures for cache isolation and network blocking."""

import socket
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolate_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Force cache writes into per-test temporary directory."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("POLISH_PIT_CALCULATOR_CACHE_DIR", str(cache_dir))


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block network access in all tests."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        """Raise explicit error when any test attempts network access."""
        raise AssertionError("Network access is blocked in tests.")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def build_year_df(
    year: int,
    usd: float = 4.0,
    eur: float = 4.5,
) -> pd.DataFrame:
    """Build compact exchange-rate dataframe for one year."""
    df = pd.DataFrame(
        {
            "_1USD": [usd, usd + 0.1],
            "_1EUR": [eur, eur + 0.1],
        },
        index=[date(year, 1, 2), date(year, 1, 3)],
    )
    return df.rename_axis(index="Date")
