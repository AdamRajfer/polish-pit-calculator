from datetime import date, datetime

import pandas as pd


def _try_to_cast_string_to_float(x) -> float | None:
    try:
        assert "," in x
        return float(x.replace(",", "."))
    except (AttributeError, ValueError, AssertionError, TypeError):
        return None


def fetch_exchange_rates(min_year: int) -> dict[str, dict[date, float]]:
    df_list: list[pd.DataFrame] = []
    for year in range(min_year, datetime.now().year + 1):
        df = (
            pd.read_csv(
                f"https://static.nbp.pl/dane/kursy/Archiwum/archiwum_tab_a_{year}.csv",
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
        df_list.append(df)
    exchange_rates_df = pd.concat(df_list).sort_index().shift()
    exchange_rates = {
        "USD": exchange_rates_df["_1USD"].to_dict(),
        "EUR": exchange_rates_df["_1EUR"].to_dict(),
    }
    return exchange_rates


def get_exchange_rate(
    currency: str,
    date_: date,
    exchange_rates: dict[str, dict[date, float]],
) -> float:
    exchange_rates_currency = exchange_rates[currency]
    if date_ in exchange_rates_currency:
        return exchange_rates_currency[date_]
    date_ = sorted(filter(lambda x: x < date_, exchange_rates_currency))[-1]
    return exchange_rates_currency[date_]
