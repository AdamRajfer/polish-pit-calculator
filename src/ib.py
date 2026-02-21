"""Interactive Brokers Trade Cash reporter implementation (IB Flex Query API)."""

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter
from src.utils import get_exchange_rate


class IBTradeCashTaxReporter(TaxReporter):
    """Generate tax records using the IB Flex Query API under the legacy reporter name."""

    SEND_REQUEST_URL = (
        "https://ndcdyn.interactivebrokers.com/AccountManagement/" + "FlexWebService/SendRequest"
    )
    DEFAULT_GET_STATEMENT_URL = (
        "https://gdcdyn.interactivebrokers.com/AccountManagement/" + "FlexWebService/GetStatement"
    )
    EMPTY_STATEMENT_XML = (
        "<FlexQueryResponse><FlexStatements count='0'></FlexStatements>" + "</FlexQueryResponse>"
    )

    def __init__(self, query_id: str, token: str) -> None:
        """Store Flex Query id and API token."""
        super().__init__()
        self.query_id = str(query_id).strip()
        self.token = token.strip()

    def generate(self) -> TaxReport:
        """Build yearly tax report from trades and cash transactions."""
        trades: list[dict[str, str]] = []
        cash: list[dict[str, str]] = []
        for statement_trades, statement_cash in self._iter_statement_entries():
            trades.extend(statement_trades)
            cash.extend(statement_cash)

        trades_df = self._build_trades_dataframe(trades)
        cash_df = self._build_cash_dataframe(cash)
        trade_revenue = (
            trades_df.groupby("Year")["sell_price_pln"].sum()
            if trades_df is not None and not trades_df.empty
            else pd.Series(dtype=float)
        )
        trade_cost = (
            trades_df.groupby("Year")["buy_price_pln"].sum()
            if trades_df is not None and not trades_df.empty
            else pd.Series(dtype=float)
        )
        interest_income = (
            cash_df.groupby("Year")["income_pln"].sum()
            if cash_df is not None and not cash_df.empty
            else pd.Series(dtype=float)
        )
        interest_wtax = (
            cash_df.groupby("Year")["withholding_pln"].sum()
            if cash_df is not None and not cash_df.empty
            else pd.Series(dtype=float)
        )

        years = set(trade_revenue.index).union(
            trade_cost.index,
            interest_income.index,
            interest_wtax.index,
        )
        if not years:
            return TaxReport()

        report = TaxReport()
        for year in range(int(min(years)), datetime.now().year + 1):
            report[year] = TaxRecord(
                trade_revenue=float(trade_revenue.get(year, 0.0)),
                trade_cost=float(trade_cost.get(year, 0.0)),
                foreign_interest=float(interest_income.get(year, 0.0)),
                foreign_interest_withholding_tax=float(interest_wtax.get(year, 0.0)),
            )
        return report

    def _iter_statement_entries(self):
        """Iterate backward by year and yield parsed statement entries."""
        today = datetime.now().date()
        year = today.year
        seen_non_empty = False
        current_entries = self._resolve_current_year_entries(
            self.query_id,
            self.token,
            today,
        )

        while True:
            from_date = date(year, 1, 1)
            if year == today.year:
                entries = current_entries
            else:
                entries = self._parse_statement_entries(
                    self._fetch_statement_xml(
                        self.query_id,
                        self.token,
                        from_date.strftime("%Y%m%d"),
                        date(year, 12, 31).strftime("%Y%m%d"),
                    )
                )

            if not any(entries):
                if seen_non_empty:
                    return
            else:
                seen_non_empty = True
                yield entries
            year -= 1

    def _resolve_current_year_entries(
        self,
        query_id: str,
        token: str,
        today: date,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Find latest available YTD snapshot for current year."""
        from_date = date(today.year, 1, 1)
        to_date = today
        fd = from_date.strftime("%Y%m%d")
        while to_date >= from_date:
            if any(
                entries := self._parse_statement_entries(
                    self._fetch_statement_xml(
                        query_id,
                        token,
                        fd,
                        to_date.strftime("%Y%m%d"),
                    )
                )
            ):
                return entries
            to_date -= timedelta(days=1)
        return [], []

    def _fetch_statement_xml(self, query_id: str, token: str, fd: str, td: str) -> str:
        """Fetch one statement XML for given query and date range."""
        params = {"t": token, "q": query_id, "v": "3", "fd": fd, "td": td}
        send_url = f"{self.SEND_REQUEST_URL}?{urllib.parse.urlencode(params)}"
        ref, stmt_url, empty = self._send_request_with_retry(send_url)
        if empty:
            return self.EMPTY_STATEMENT_XML
        if ref is None:
            raise ValueError("IBKR SendRequest returned no reference code.")

        params = {"t": token, "q": ref, "v": "3"}
        get_url = (
            f"{stmt_url or self.DEFAULT_GET_STATEMENT_URL}?" f"{urllib.parse.urlencode(params)}"
        )
        return self._fetch_statement_with_retry(get_url)

    def _send_request_with_retry(
        self,
        url: str,
        retries: int = 5,
        wait_seconds: float = 5.0,
    ) -> tuple[str | None, str | None, bool]:
        """Call SendRequest endpoint with retry on IB throttling."""
        for _ in range(retries):
            root = ET.fromstring(self._fetch_url(url))
            status = root.findtext("Status")
            error_code = root.findtext("ErrorCode")
            match (status, error_code):
                case ("Success" | "Warn", _) if (ref := root.findtext("ReferenceCode")):
                    return ref, root.findtext("Url"), False
                case (_, "1003"):
                    return None, None, True
                case (_, "1018"):
                    time.sleep(wait_seconds)
                    continue
                case _:
                    raise ValueError("IBKR SendRequest failed.")
        raise ValueError("IBKR SendRequest rate-limited.")

    def _fetch_statement_with_retry(
        self,
        url: str,
        retries: int = 20,
        wait_seconds: float = 3.0,
    ) -> str:
        """Poll GetStatement endpoint until statement is ready."""
        for _ in range(retries):
            xml = self._fetch_url(url)
            root = ET.fromstring(xml)
            if root.tag != "FlexStatementResponse":
                return xml
            status = root.findtext("Status")
            error_code = root.findtext("ErrorCode")
            match (status, error_code):
                case ("Success", _):
                    return xml
                case ("Warn", _) | (_, "1018" | "1019"):
                    time.sleep(wait_seconds)
                    continue
                case _:
                    raise ValueError(f"IBKR GetStatement failed: {status}")
        raise ValueError("IBKR GetStatement did not complete in time.")

    def _fetch_url(self, url: str) -> str:
        """Fetch URL body as UTF-8 text."""
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "polish-pit-calculator/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")

    def _parse_statement_entries(
        self,
        xml: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Parse trades and cash transactions from statement XML."""
        root = ET.fromstring(xml)
        if (stmt := root.find(".//FlexStatement")) is None:
            return [], []
        cash_rows = stmt.findall("CashTransactions/CashTransaction")
        return (
            [row.attrib for row in stmt.findall("Trades/Trade")],
            [row.attrib for row in cash_rows],
        )

    def _build_trades_dataframe(self, trades: list[dict[str, str]]) -> pd.DataFrame | None:
        """Convert raw trade entries into matched FIFO transaction rows."""
        if not trades:
            return None

        raw = pd.DataFrame(trades)
        quantity = pd.to_numeric(raw["quantity"], errors="coerce")
        proceeds = pd.to_numeric(raw["proceeds"], errors="coerce")
        commission = pd.Series(0.0, index=raw.index)
        if "ibCommission" in raw:
            commission = pd.to_numeric(raw["ibCommission"], errors="coerce")
            commission = commission.fillna(0.0)

        valid = quantity.notna() & proceeds.notna() & quantity.ne(0)
        if not valid.any():
            return None

        date_time = pd.to_datetime(raw.loc[valid, "dateTime"], format="%Y%m%d;%H%M%S")
        qty = quantity.loc[valid]
        df = pd.DataFrame(
            {
                "DateTime": date_time,
                "Year": date_time.dt.year,
                "Currency": raw.loc[valid, "currency"],
                "Symbol": raw.loc[valid, "symbol"],
                "Quantity": qty.abs(),
                "IsBuy": qty.gt(0),
                "Price": (proceeds.loc[valid] + commission.loc[valid]) / -qty,
            }
        ).sort_values("DateTime", ignore_index=True)

        trades_df = self._fifo_match_trades(df)
        return trades_df if not trades_df.empty else None

    def _build_cash_dataframe(self, cash: list[dict[str, str]]) -> pd.DataFrame | None:
        """Convert raw cash entries into income and withholding rows."""
        if not cash:
            return None

        raw = pd.DataFrame(cash)
        raw_dt = raw["dateTime"].astype(str)
        full_dt = raw_dt.where(~raw_dt.str.fullmatch(r"\d{8}"), raw_dt + ";000000")
        date_time = pd.to_datetime(full_dt, format="%Y%m%d;%H%M%S")
        amount = pd.to_numeric(raw["amount"], errors="coerce").round(2)
        valid = amount.notna()
        if not valid.any():
            return None

        raw_type = raw.loc[valid, "type"].fillna("").astype(str).str.lower()
        df = pd.DataFrame(
            {
                "Currency": raw.loc[valid, "currency"],
                "Description": raw.loc[valid, "description"],
                "Type": raw_type,
                "Date": date_time.loc[valid].dt.date,
                "Year": date_time.loc[valid].dt.year,
                "Amount": amount.loc[valid],
            }
        )

        df["fx"] = df.apply(
            lambda row: get_exchange_rate(row["Currency"], row["Date"]),
            axis=1,
        )

        wtax = df[df["Type"].str.contains("withholding")].copy()
        dividends = self._merge_income_with_withholding(
            df[df["Type"].str.contains("dividend")],
            wtax,
            r"\s*\([^()]*\)\s*$",
            (r"\s-\s?.*$", True),
        )
        interests = self._merge_income_with_withholding(
            df[df["Type"].str.contains("interest")],
            wtax,
            r"^[A-Z]{3}\s+",
            (r"^.*?\bon\b\s*", False),
        )
        cash_df = pd.concat([dividends, interests], ignore_index=True)
        if cash_df.empty:
            return None
        cash_df["income_pln"] = cash_df["Amount"] * cash_df["fx"]
        cash_df["withholding_pln"] = cash_df["Amount_wtax"] * cash_df["fx"]
        return cash_df

    def _fifo_match_trades(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """FIFO-match buys against sells and return realized trade rows."""
        trades_fifo: list[dict[str, float | int]] = []
        for _, symbol_df in df.groupby("Symbol"):
            buys = symbol_df[symbol_df["IsBuy"]].reset_index(drop=True).copy()
            sells = symbol_df[~symbol_df.IsBuy].reset_index(drop=True).copy()
            buy_idx = sell_idx = 0
            while buy_idx < len(buys) and sell_idx < len(sells):
                buy = buys.iloc[buy_idx]
                sell = sells.iloc[sell_idx]
                buy_fx = get_exchange_rate(
                    buy["Currency"],
                    buy["DateTime"].date(),
                )
                sell_fx = get_exchange_rate(
                    sell["Currency"],
                    sell["DateTime"].date(),
                )
                if buy["Quantity"] == sell["Quantity"]:
                    qty = buy["Quantity"]
                    buy_idx += 1
                    sell_idx += 1
                elif buy["Quantity"] < sell["Quantity"]:
                    qty = buy["Quantity"]
                    sells.at[sell_idx, "Quantity"] -= qty
                    buy_idx += 1
                else:
                    qty = sell["Quantity"]
                    buys.at[buy_idx, "Quantity"] -= qty
                    sell_idx += 1
                buy_amount = buy["Price"] * qty
                sell_amount = sell["Price"] * qty
                trades_fifo.append(
                    {
                        "buy_price": buy_amount,
                        "buy_price_pln": buy_amount * buy_fx,
                        "sell_price": sell_amount,
                        "sell_price_pln": sell_amount * sell_fx,
                        "Year": sell["Year"],
                    }
                )
        return pd.DataFrame(trades_fifo)

    def _merge_income_with_withholding(
        self,
        income_df: pd.DataFrame,
        wtax_df: pd.DataFrame,
        income_desc_regex: str,
        wtax_pattern_case: tuple[str, bool],
    ) -> pd.DataFrame:
        """Normalize descriptions and attach matching withholding entries."""
        if income_df.empty:
            return income_df.iloc[0:0]

        income = income_df.copy().sort_values(
            by=["Date", "Amount", "Description"],
            ascending=[True, False, True],
            kind="mergesort",
        )
        income = income.drop(columns=["Year"], errors="ignore")
        income["Description"] = income["Description"].str.replace(
            income_desc_regex,
            "",
            regex=True,
        )

        if not wtax_df.empty:
            wtax = wtax_df.copy().sort_values(
                by=["Date", "Amount", "Description"],
                ascending=[True, False, True],
                kind="mergesort",
            )
            wtax_desc_regex, wtax_case = wtax_pattern_case
            wtax["Description"] = wtax["Description"].str.replace(
                wtax_desc_regex,
                "",
                regex=True,
                case=wtax_case,
            )
            income = income.merge(
                wtax[["Currency", "Description", "Amount", "Year"]],
                on=["Currency", "Description"],
                how="left",
                suffixes=("", "_wtax"),
            )
        else:
            income["Amount_wtax"] = 0.0
            income["Year"] = pd.NA

        income["Amount_wtax"] = income["Amount_wtax"].fillna(0.0).abs()
        return income
