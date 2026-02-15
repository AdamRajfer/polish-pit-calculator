from datetime import datetime
from io import StringIO

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import fetch_exchange_rates, get_exchange_rate


class IBTradeCashTaxReporter(TaxReporter):
    def generate(self) -> TaxReport:
        trades = self._load_trades()
        dividends = self._load_dividends_or_interests(
            prefix="Dividends",
            pattern=r"\s*\([^()]*\)\s*$",
            wtax_pattern=r"\s-\s?.*$",
        )
        interests = self._load_dividends_or_interests(
            prefix="Interest",
            pattern=r"^[A-Z]+\s+",
            wtax_pattern=r"^.*?\bon\b\s*",
        )
        min_year = int(
            min(df["Year"].min() for df in [trades, dividends, interests])
        )

        tax_report = TaxReport()
        for year in range(min_year, datetime.now().year + 1):
            revenue = 0.0
            cost = 0.0
            interest = 0.0
            interest_wtax = 0.0
            dividend = 0.0
            dividend_wtax = 0.0

            if trades is not None:
                trades_year = trades[trades["Year"] == year]
                revenue = trades_year["sell_price_pln"].sum()
                cost = trades_year["buy_price_pln"].sum()

            if dividends is not None:
                dividends_year = dividends[dividends["Year"] == year]
                dividend = dividends_year["Amount_pln"].sum()
                dividend_wtax = dividends_year["Amount_wtax_pln"].sum()

            if interests is not None:
                interests_year = interests[interests["Year"] == year]
                interest = interests_year["Amount_pln"].sum()
                interest_wtax = interests_year["Amount_wtax_pln"].sum()

            tax_report[year] = TaxRecord(
                trade_revenue=revenue,
                trade_cost=cost,
                foreign_interest=interest + dividend,
                foreign_interest_withholding_tax=interest_wtax + dividend_wtax,
            )

        return tax_report

    def _load_trades(self) -> pd.DataFrame:
        df = self._load_report("Trades", "Date/Time")
        df = (
            df[df["Header"] == "Data"]
            .sort_values(by=["Date/Time"])
            .reset_index(drop=True)
        )
        df["Quantity"] = (
            df["Quantity"]
            .apply(lambda x: x.replace(",", "") if isinstance(x, str) else x)
            .astype(float)
        )
        df["Type"] = df["Quantity"].apply(lambda x: "BUY" if x > 0 else "SELL")
        df["Price"] = (df["Proceeds"] + df["Comm/Fee"]) / -df["Quantity"]
        df["Quantity"] = df["Quantity"].abs()
        min_year = df["Date/Time"].dt.date.min().year
        exc_rates = fetch_exchange_rates(min_year)
        trades = []
        for _, x in df.groupby("Symbol"):
            x = x.sort_values("Date/Time")
            x_buy = x[x["Type"] == "BUY"].reset_index(drop=True)
            x_sell = x[x["Type"] == "SELL"].reset_index(drop=True)
            trade = []
            buy_idx = 0
            sell_idx = 0
            while buy_idx < len(x_buy) and sell_idx < len(x_sell):
                buy = x_buy.iloc[buy_idx]
                buy_exchange_rate = get_exchange_rate(
                    buy["Currency"],
                    buy["Date/Time"].date(),
                    exc_rates,
                )
                sell = x_sell.iloc[sell_idx]
                sell_exchange_rate = get_exchange_rate(
                    sell["Currency"],
                    sell["Date/Time"].date(),
                    exc_rates,
                )
                if buy["Quantity"] == sell["Quantity"]:
                    buy_amount = buy["Price"] * buy["Quantity"]
                    sell_amount = sell["Price"] * buy["Quantity"]
                    record = dict(
                        buy_price=buy_amount,
                        buy_price_pln=buy_amount * buy_exchange_rate,
                        sell_price=sell_amount,
                        sell_price_pln=sell_amount * sell_exchange_rate,
                        Year=sell["Year"],
                    )
                    trade.append(record)
                    buy_idx += 1
                    sell_idx += 1
                elif buy["Quantity"] < sell["Quantity"]:
                    sell = sell.copy()
                    sell["Quantity"] = buy["Quantity"]
                    x_sell.at[sell_idx, "Quantity"] -= buy["Quantity"]
                    buy_amount = buy["Price"] * buy["Quantity"]
                    sell_amount = sell["Price"] * buy["Quantity"]
                    record = dict(
                        buy_price=buy_amount,
                        buy_price_pln=buy_amount * buy_exchange_rate,
                        sell_price=sell_amount,
                        sell_price_pln=sell_amount * sell_exchange_rate,
                        Year=sell["Year"],
                    )
                    trade.append(record)
                    buy_idx += 1
                else:
                    buy = buy.copy()
                    buy["Quantity"] = sell["Quantity"]
                    x_buy.at[buy_idx, "Quantity"] -= sell["Quantity"]
                    buy_amount = buy["Price"] * sell["Quantity"]
                    sell_amount = sell["Price"] * sell["Quantity"]
                    record = dict(
                        buy_price=buy_amount,
                        buy_price_pln=buy_amount * buy_exchange_rate,
                        sell_price=sell_amount,
                        sell_price_pln=sell_amount * sell_exchange_rate,
                        Year=sell["Year"],
                    )
                    trade.append(record)
                    sell_idx += 1
            trades.extend(trade)
        return pd.DataFrame(trades)

    def _load_dividends_or_interests(
        self,
        prefix: str,
        pattern: str,
        wtax_pattern: str,
    ) -> pd.DataFrame:
        df = self._load_report(prefix, "Date", pattern)
        df["Date"] = df["Date"].dt.date
        wtax = self._load_report("Withholding Tax", "Date", wtax_pattern)
        wtax["Date"] = wtax["Date"].dt.date
        df = df[["Currency", "Date", "Description", "Amount"]].merge(
            wtax,
            on=["Currency", "Description"],
            how="left",
            suffixes=("", "_wtax"),
        )
        df = df.fillna({"Amount": 0.0, "Amount_wtax": 0.0})
        df["Amount_wtax"] = df["Amount_wtax"].abs()
        min_year = int(min(df["Year"].min(), wtax["Year"].min()))
        exc_rates = fetch_exchange_rates(min_year)
        exc_rate = df.apply(
            lambda x: get_exchange_rate(x["Currency"], x["Date"], exc_rates),
            axis=1,
        )
        df["Amount_pln"] = df["Amount"] * exc_rate
        df["Amount_wtax_pln"] = df["Amount_wtax"] * exc_rate
        return df

    def _load_report(
        self, prefix: str, date_col: str, regex: str | None = None
    ) -> pd.DataFrame:
        reports: list[pd.DataFrame] = []
        for arg in self.args:
            content = arg.getvalue().decode("utf-8")
            io = "".join(
                x
                for x in content.splitlines(True)
                if x.startswith(f"{prefix},")
            )
            if not io:
                continue
            string_io = StringIO(io)
            report = pd.read_csv(string_io, parse_dates=[date_col])
            reports.append(report)
        df = pd.concat(reports, ignore_index=True)
        df = df[df[date_col].notna()]
        df["Year"] = df[date_col].apply(lambda x: x.year)
        if regex is not None:
            df["Description"] = df["Description"].str.replace(
                regex, "", regex=True
            )
        return df
