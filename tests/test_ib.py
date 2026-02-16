"""Tests for Interactive Brokers Trade Cash reporter (Flex API implementation)."""

from datetime import date, datetime
from typing import Any, cast
from unittest import TestCase
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlparse

import pandas as pd
from pandas.testing import assert_frame_equal

from src.config import TaxRecord, TaxReport
from src.ib import IBTradeCashTaxReporter


def _private(reporter: IBTradeCashTaxReporter, name: str) -> Any:
    """Return a private reporter callable by name."""
    return getattr(reporter, name)


def _xml_attrs(attrs: dict[str, str]) -> str:
    return " ".join(f"{k}='{v}'" for k, v in attrs.items())


def _statement_xml(
    trades: list[dict[str, str]] | None = None,
    cash: list[dict[str, str]] | None = None,
) -> str:
    trades = trades or []
    cash = cash or []
    trade_rows = "".join(f"<Trade {_xml_attrs(row)}/>" for row in trades)
    cash_rows = "".join(f"<CashTransaction {_xml_attrs(row)}/>" for row in cash)
    return (
        "<FlexQueryResponse><FlexStatements count='1'><FlexStatement>"
        f"<Trades>{trade_rows}</Trades>"
        f"<CashTransactions>{cash_rows}</CashTransactions>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )


def _send_response(
    status: str,
    error_code: str | None = None,
    reference: str | None = None,
    url: str | None = None,
) -> str:
    parts = ["<FlexWebServiceResponse>"]
    parts.append(f"<Status>{status}</Status>")
    if error_code is not None:
        parts.append(f"<ErrorCode>{error_code}</ErrorCode>")
    if reference is not None:
        parts.append(f"<ReferenceCode>{reference}</ReferenceCode>")
    if url is not None:
        parts.append(f"<Url>{url}</Url>")
    parts.append("</FlexWebServiceResponse>")
    return "".join(parts)


def _statement_response(status: str, error_code: str | None = None) -> str:
    parts = ["<FlexStatementResponse>", f"<Status>{status}</Status>"]
    if error_code is not None:
        parts.append(f"<ErrorCode>{error_code}</ErrorCode>")
    parts.append("</FlexStatementResponse>")
    return "".join(parts)


class TestIBTradeCashTaxReporter(TestCase):
    """Test parsing, request retries and dataframe building."""

    def _reporter(self) -> IBTradeCashTaxReporter:
        return IBTradeCashTaxReporter("query-id", "token")

    def test_parse_statement_entries(self) -> None:
        """Test parsing trades and cash rows from one statement."""
        reporter = self._reporter()
        xml = _statement_xml(
            trades=[{"symbol": "AAPL", "quantity": "1"}],
            cash=[{"type": "Dividends", "amount": "1"}],
        )

        trades, cash = _private(reporter, "_parse_statement_entries")(xml)

        self.assertEqual(trades, [{"symbol": "AAPL", "quantity": "1"}])
        self.assertEqual(cash, [{"type": "Dividends", "amount": "1"}])

    def test_parse_statement_entries_without_flex_statement(self) -> None:
        """Test parser returns empty tuples when no statement exists."""
        reporter = self._reporter()
        xml = "<FlexQueryResponse><FlexStatements count='0'/></FlexQueryResponse>"

        trades, cash = _private(reporter, "_parse_statement_entries")(xml)

        self.assertEqual((trades, cash), ([], []))

    def test_send_request_with_retry_success(self) -> None:
        """Test SendRequest success path returns reference and URL."""
        reporter = self._reporter()
        response = _send_response(
            status="Success",
            reference="REF-1",
            url="https://ibkr.example/GetStatement",
        )
        with patch.object(reporter, "_fetch_url", return_value=response):
            ref, stmt_url, empty = _private(reporter, "_send_request_with_retry")("x")

        self.assertEqual(
            (ref, stmt_url, empty),
            (
                "REF-1",
                "https://ibkr.example/GetStatement",
                False,
            ),
        )

    def test_send_request_with_retry_empty_marker(self) -> None:
        """Test SendRequest empty marker short-circuits statement fetch."""
        reporter = self._reporter()
        response = _send_response(status="Fail", error_code="1003")
        with patch.object(reporter, "_fetch_url", return_value=response):
            result = _private(reporter, "_send_request_with_retry")("x")

        self.assertEqual(result, (None, None, True))

    def test_send_request_with_retry_retries_1018_then_success(self) -> None:
        """Test SendRequest retries throttling and eventually succeeds."""
        reporter = self._reporter()
        responses = [
            _send_response(status="Warn", error_code="1018"),
            _send_response(status="Warn", reference="REF-2"),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch("src.ib.time.sleep") as sleep:
                result = _private(reporter, "_send_request_with_retry")("x")

        self.assertEqual(result, ("REF-2", None, False))
        sleep.assert_called_once()

    def test_send_request_with_retry_rate_limited(self) -> None:
        """Test SendRequest raises after repeated 1018 responses."""
        reporter = self._reporter()
        responses = [
            _send_response(status="Warn", error_code="1018"),
            _send_response(status="Warn", error_code="1018"),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch("src.ib.time.sleep"):
                with self.assertRaisesRegex(ValueError, "rate-limited"):
                    _private(reporter, "_send_request_with_retry")("x", retries=2)

    def test_send_request_with_retry_unknown_error_raises(self) -> None:
        """Test SendRequest raises on unknown hard failure."""
        reporter = self._reporter()
        response = _send_response(status="Fail", error_code="9999")
        with patch.object(reporter, "_fetch_url", return_value=response):
            with self.assertRaisesRegex(ValueError, "SendRequest failed"):
                _private(reporter, "_send_request_with_retry")("x")

    def test_fetch_statement_with_retry_returns_statement_payload(
        self,
    ) -> None:
        """Test GetStatement returns payload once non-status XML arrives."""
        reporter = self._reporter()
        payload = _statement_xml(
            trades=[{"symbol": "AAPL", "quantity": "1"}],
            cash=[],
        )
        with patch.object(reporter, "_fetch_url", return_value=payload):
            result = _private(reporter, "_fetch_statement_with_retry")("x")

        self.assertEqual(result, payload)

    def test_fetch_statement_with_retry_warn_then_payload(self) -> None:
        """Test GetStatement retries warn status and returns payload."""
        reporter = self._reporter()
        responses = [
            _statement_response(status="Warn", error_code="1018"),
            _statement_xml(),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch("src.ib.time.sleep") as sleep:
                result = _private(reporter, "_fetch_statement_with_retry")("x")

        self.assertEqual(result, responses[1])
        sleep.assert_called_once()

    def test_fetch_statement_with_retry_failure_raises(self) -> None:
        """Test GetStatement raises on hard failure status."""
        reporter = self._reporter()
        response = _statement_response(status="Fail", error_code="1020")
        with patch.object(reporter, "_fetch_url", return_value=response):
            with self.assertRaisesRegex(ValueError, "GetStatement failed"):
                _private(reporter, "_fetch_statement_with_retry")("x")

    def test_fetch_statement_with_retry_success_status_payload(self) -> None:
        """Test success status on FlexStatementResponse returns XML immediately."""
        reporter = self._reporter()
        response = _statement_response(status="Success")
        with patch.object(reporter, "_fetch_url", return_value=response):
            result = _private(reporter, "_fetch_statement_with_retry")("x")
        self.assertEqual(result, response)

    def test_fetch_statement_with_retry_timeout_raises(self) -> None:
        """Test GetStatement raises timeout after repeated retryable statuses."""
        reporter = self._reporter()
        response = _statement_response(status="Warn", error_code="1018")
        with patch.object(reporter, "_fetch_url", return_value=response):
            with patch("src.ib.time.sleep"):
                with self.assertRaisesRegex(ValueError, "did not complete in time"):
                    _private(reporter, "_fetch_statement_with_retry")("x", retries=2)

    def test_fetch_statement_xml_builds_send_and_get_urls(self) -> None:
        """Test statement XML request builds expected Send/Get URLs."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_send_request_with_retry",
            return_value=("REF-3", "https://ibkr.example/GetStatement", False),
        ) as send_req:
            with patch.object(
                reporter,
                "_fetch_statement_with_retry",
                return_value="<xml/>",
            ) as get_stmt:
                result = _private(reporter, "_fetch_statement_xml")(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        self.assertEqual(result, "<xml/>")
        send_url = send_req.call_args.args[0]
        send_query = parse_qs(urlparse(send_url).query)
        self.assertEqual(
            send_query,
            {
                "t": ["token"],
                "q": ["query-id"],
                "v": ["3"],
                "fd": ["20250101"],
                "td": ["20251231"],
            },
        )

        get_url = get_stmt.call_args.args[0]
        get_query = parse_qs(urlparse(get_url).query)
        self.assertEqual(get_query, {"t": ["token"], "q": ["REF-3"], "v": ["3"]})

    def test_fetch_statement_xml_raises_when_reference_missing(self) -> None:
        """Test statement request raises when response has no reference code."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_send_request_with_retry",
            return_value=(None, None, False),
        ):
            with self.assertRaisesRegex(ValueError, "no reference code"):
                _private(reporter, "_fetch_statement_xml")(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

    def test_fetch_statement_xml_uses_default_get_url(self) -> None:
        """Test default GetStatement URL is used when Send has none."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_send_request_with_retry",
            return_value=("REF-4", None, False),
        ):
            with patch.object(
                reporter,
                "_fetch_statement_with_retry",
                return_value="<xml/>",
            ) as get_stmt:
                _private(reporter, "_fetch_statement_xml")(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        get_url = get_stmt.call_args.args[0]
        parsed = urlparse(get_url)
        self.assertEqual(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            reporter.DEFAULT_GET_STATEMENT_URL,
        )
        self.assertEqual(parse_qs(parsed.query), {"t": ["token"], "q": ["REF-4"], "v": ["3"]})

    def test_fetch_statement_xml_returns_empty_marker_without_get_call(
        self,
    ) -> None:
        """Test empty marker response skips GetStatement request."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_send_request_with_retry",
            return_value=(None, None, True),
        ):
            with patch.object(
                reporter,
                "_fetch_statement_with_retry",
                return_value="<xml/>",
            ) as get_stmt:
                result = _private(reporter, "_fetch_statement_xml")(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        self.assertEqual(result, reporter.EMPTY_STATEMENT_XML)
        get_stmt.assert_not_called()


class TestIBTradeCashTaxReporterDataAndIteration(TestCase):
    """Test statement iteration and dataframe/cash processing paths."""

    def _reporter(self) -> IBTradeCashTaxReporter:
        return IBTradeCashTaxReporter("query-id", "token")

    def _sample_trades(self) -> list[dict[str, str]]:
        return [
            {
                "currency": "USD",
                "symbol": "AAPL",
                "dateTime": "20250102;120000",
                "quantity": "10",
                "proceeds": "-1000",
                "ibCommission": "-2",
            },
            {
                "currency": "USD",
                "symbol": "AAPL",
                "dateTime": "20250110;120000",
                "quantity": "-6",
                "proceeds": "720",
                "ibCommission": "-1.2",
            },
            {
                "currency": "USD",
                "symbol": "AAPL",
                "dateTime": "20250111;120000",
                "quantity": "-4",
                "proceeds": "500",
                "ibCommission": "-0.8",
            },
        ]

    def _sample_cash(self) -> list[dict[str, str]]:
        return [
            {
                "currency": "USD",
                "description": "ACME CORP (US000)",
                "dateTime": "20250105",
                "amount": "10.00",
                "type": "Dividends",
            },
            {
                "currency": "USD",
                "description": "ACME CORP - Withholding Tax",
                "dateTime": "20250105;120000",
                "amount": "-1.00",
                "type": "Withholding Tax",
            },
            {
                "currency": "USD",
                "description": "USD CASH BALANCE",
                "dateTime": "20250106;130000",
                "amount": "20.00",
                "type": "Bond Interest Received",
            },
            {
                "currency": "USD",
                "description": "Tax withheld on CASH BALANCE",
                "dateTime": "20250106;130500",
                "amount": "-2.00",
                "type": "Withholding Tax",
            },
        ]

    def test_fetch_url_reads_and_decodes_response_body(self) -> None:
        """Test URL helper uses Request and decodes bytes as UTF-8 text."""
        reporter = self._reporter()

        class _Response:
            def __enter__(self):
                """Return self to support context-manager protocol."""
                return self

            def __exit__(self, *_args):
                """Return False so exceptions are not suppressed."""
                return False

            def read(self) -> bytes:
                """Return encoded XML payload body."""
                return b"<xml/>"

        with patch("src.ib.urllib.request.urlopen", return_value=_Response()) as open_:
            xml = _private(reporter, "_fetch_url")("https://example.test")

        self.assertEqual(xml, "<xml/>")
        request = open_.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test")

    def test_resolve_current_year_entries_walks_back_until_non_empty(
        self,
    ) -> None:
        """Test current-year resolver walks back day by day until data."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_fetch_statement_xml",
            side_effect=["day1", "day2", "day3"],
        ) as fetch_xml:
            with patch.object(
                reporter,
                "_parse_statement_entries",
                side_effect=[([], []), ([], []), ([{"symbol": "AAPL"}], [])],
            ):
                entries = _private(reporter, "_resolve_current_year_entries")(
                    "query-id",
                    "token",
                    date(2026, 2, 14),
                )

        self.assertEqual(entries, ([{"symbol": "AAPL"}], []))
        self.assertEqual(
            fetch_xml.call_args_list,
            [
                call("query-id", "token", "20260101", "20260214"),
                call("query-id", "token", "20260101", "20260213"),
                call("query-id", "token", "20260101", "20260212"),
            ],
        )

    def test_resolve_current_year_entries_returns_empty_when_no_data(self) -> None:
        """Test current-year resolver returns empty tuple when all days are empty."""
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_fetch_statement_xml",
            side_effect=["day1", "day2"],
        ) as fetch_xml:
            with patch.object(
                reporter,
                "_parse_statement_entries",
                side_effect=[([], []), ([], [])],
            ):
                entries = _private(reporter, "_resolve_current_year_entries")(
                    "query-id",
                    "token",
                    date(2026, 1, 2),
                )

        self.assertEqual(entries, ([], []))
        self.assertEqual(
            fetch_xml.call_args_list,
            [
                call("query-id", "token", "20260101", "20260102"),
                call("query-id", "token", "20260101", "20260101"),
            ],
        )

    def test_iter_statement_entries_skips_initial_empty_current_year(self) -> None:
        """Test yearly iterator continues after empty current year before first data year."""
        reporter = self._reporter()
        with patch("src.ib.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2026, 2, 14, 12, 0, 0)
            with patch.object(
                reporter,
                "_resolve_current_year_entries",
                return_value=([], []),
            ):
                with patch.object(
                    reporter,
                    "_fetch_statement_xml",
                    side_effect=["2025", "2024"],
                ) as fetch_xml:
                    with patch.object(
                        reporter,
                        "_parse_statement_entries",
                        side_effect=[([{"id": "previous"}], []), ([], [])],
                    ):
                        entries = list(_private(reporter, "_iter_statement_entries")())

        self.assertEqual(entries, [([{"id": "previous"}], [])])
        self.assertEqual(
            fetch_xml.call_args_list,
            [
                call("query-id", "token", "20250101", "20251231"),
                call("query-id", "token", "20240101", "20241231"),
            ],
        )

    def test_iter_statement_entries_stops_after_first_empty_post_data(
        self,
    ) -> None:
        """Test iteration stops after first empty year after data years."""
        reporter = self._reporter()
        current_entries: tuple[
            list[dict[str, str]],
            list[dict[str, str]],
        ] = ([{"id": "current"}], [])
        with patch("src.ib.datetime") as dt_mock:
            dt_mock.now.return_value = datetime(2026, 2, 14, 12, 0, 0)
            with patch.object(
                reporter,
                "_resolve_current_year_entries",
                return_value=current_entries,
            ):
                with patch.object(
                    reporter,
                    "_fetch_statement_xml",
                    side_effect=["2025", "2024"],
                ) as fetch_xml:
                    with patch.object(
                        reporter,
                        "_parse_statement_entries",
                        side_effect=[([{"id": "previous"}], []), ([], [])],
                    ):
                        entries = list(_private(reporter, "_iter_statement_entries")())

        self.assertEqual(
            entries,
            [
                ([{"id": "current"}], []),
                ([{"id": "previous"}], []),
            ],
        )
        self.assertEqual(
            fetch_xml.call_args_list,
            [
                call("query-id", "token", "20250101", "20251231"),
                call("query-id", "token", "20240101", "20241231"),
            ],
        )

    @patch("src.ib.get_exchange_rate")
    def test_build_trades_dataframe_fifo(
        self,
        get_exchange_rate_mock,
    ) -> None:
        """Test FIFO trade matching produces expected PLN totals."""
        reporter = self._reporter()
        get_exchange_rate_mock.return_value = 4.0

        actual = cast(
            pd.DataFrame,
            _private(reporter, "_build_trades_dataframe")(self._sample_trades()),
        )
        expected = pd.DataFrame(
            [
                {
                    "buy_price": 601.2,
                    "buy_price_pln": 2404.8,
                    "sell_price": 718.8,
                    "sell_price_pln": 2875.2,
                    "Year": 2025,
                },
                {
                    "buy_price": 400.8,
                    "buy_price_pln": 1603.2,
                    "sell_price": 499.2,
                    "sell_price_pln": 1996.8,
                    "Year": 2025,
                },
            ]
        )
        assert_frame_equal(actual.reset_index(drop=True), expected, check_dtype=False)

    def test_build_trades_dataframe_invalid_rows(self) -> None:
        """Test invalid trade rows are filtered to no dataframe output."""
        reporter = self._reporter()
        rows = [
            {
                "currency": "USD",
                "symbol": "AAPL",
                "dateTime": "20250102;120000",
                "quantity": "bad",
                "proceeds": "bad",
            }
        ]

        df = _private(reporter, "_build_trades_dataframe")(rows)

        self.assertIsNone(df)

    @patch("src.ib.get_exchange_rate")
    def test_build_cash_dataframe_merges_and_calculates_pln(
        self,
        get_exchange_rate_mock,
    ) -> None:
        """Test cash dataframe joins withholding and computes PLN fields."""
        reporter = self._reporter()
        get_exchange_rate_mock.return_value = 4.0

        actual = cast(
            pd.DataFrame,
            _private(reporter, "_build_cash_dataframe")(self._sample_cash()),
        )
        expected = pd.DataFrame(
            [
                {
                    "Currency": "USD",
                    "Description": "ACME CORP",
                    "Type": "dividends",
                    "Date": date(2025, 1, 5),
                    "Amount": 10.0,
                    "fx": 4.0,
                    "Amount_wtax": 1.0,
                    "Year": 2025.0,
                    "income_pln": 40.0,
                    "withholding_pln": 4.0,
                },
                {
                    "Currency": "USD",
                    "Description": "CASH BALANCE",
                    "Type": "bond interest received",
                    "Date": date(2025, 1, 6),
                    "Amount": 20.0,
                    "fx": 4.0,
                    "Amount_wtax": 2.0,
                    "Year": 2025.0,
                    "income_pln": 80.0,
                    "withholding_pln": 8.0,
                },
            ]
        )
        assert_frame_equal(actual.reset_index(drop=True), expected, check_dtype=False)

    def test_build_cash_dataframe_returns_none_for_invalid_amounts(self) -> None:
        """Test cash dataframe returns None when all amounts are invalid."""
        reporter = self._reporter()
        rows = [
            {
                "currency": "USD",
                "description": "ACME",
                "dateTime": "20250105;120000",
                "amount": "not-a-number",
                "type": "Dividends",
            }
        ]
        df = _private(reporter, "_build_cash_dataframe")(rows)
        self.assertIsNone(df)

    @patch("src.ib.get_exchange_rate", return_value=4.0)
    def test_build_cash_dataframe_returns_none_for_non_income_types(
        self,
        _rate: object,
    ) -> None:
        """Test cash dataframe returns None when no dividend/interest rows exist."""
        reporter = self._reporter()
        rows = [
            {
                "currency": "USD",
                "description": "Monthly fee",
                "dateTime": "20250105;120000",
                "amount": "-1.00",
                "type": "Broker Fees",
            }
        ]
        df = _private(reporter, "_build_cash_dataframe")(rows)
        self.assertIsNone(df)

    def test_merge_income_with_empty_withholding(self) -> None:
        """Test merge keeps income and zeroes withholding when missing."""
        reporter = self._reporter()
        income_df = pd.DataFrame(
            {
                "Currency": ["USD"],
                "Description": ["ABC CORP (US123)"],
                "Date": [date(2025, 1, 5)],
                "Year": [2025],
                "Amount": [10.0],
                "fx": [4.0],
            }
        )
        wtax_df = pd.DataFrame(columns=["Currency", "Description", "Date", "Year", "Amount"])

        result = _private(reporter, "_merge_income_with_withholding")(
            income_df,
            wtax_df,
            r"\s*\([^()]*\)\s*$",
            (r"\s-\s?.*$", True),
        )

        expected = pd.DataFrame(
            {
                "Currency": ["USD"],
                "Description": ["ABC CORP"],
                "Date": [date(2025, 1, 5)],
                "Amount": [10.0],
                "fx": [4.0],
                "Amount_wtax": [0.0],
                "Year": [pd.NA],
            }
        )
        assert_frame_equal(result, expected, check_dtype=False)

    def test_merge_income_with_withholding_empty_income(self) -> None:
        """Test merge returns empty frame immediately for empty income input."""
        reporter = self._reporter()
        empty_income = pd.DataFrame(
            columns=["Currency", "Description", "Date", "Year", "Amount", "fx"]
        )
        wtax_df = pd.DataFrame(columns=["Currency", "Description", "Date", "Year", "Amount"])

        result = _private(reporter, "_merge_income_with_withholding")(
            empty_income,
            wtax_df,
            r"x",
            (r"y", True),
        )

        assert_frame_equal(result, empty_income.iloc[0:0], check_dtype=False)

    @patch("src.ib.get_exchange_rate", return_value=1.0)
    def test_fifo_match_trades_buy_less_than_sell_branch(self, _rate: object) -> None:
        """Test FIFO branch where buy quantity is lower than sell quantity."""
        reporter = self._reporter()
        df = pd.DataFrame(
            [
                {
                    "DateTime": pd.Timestamp("2025-01-02 10:00:00"),
                    "Year": 2025,
                    "Currency": "USD",
                    "Symbol": "AAPL",
                    "Quantity": 2.0,
                    "IsBuy": True,
                    "Price": 10.0,
                },
                {
                    "DateTime": pd.Timestamp("2025-01-03 10:00:00"),
                    "Year": 2025,
                    "Currency": "USD",
                    "Symbol": "AAPL",
                    "Quantity": 3.0,
                    "IsBuy": False,
                    "Price": 12.0,
                },
            ]
        )
        actual = _private(reporter, "_fifo_match_trades")(df)
        expected = pd.DataFrame(
            [
                {
                    "buy_price": 20.0,
                    "buy_price_pln": 20.0,
                    "sell_price": 24.0,
                    "sell_price_pln": 24.0,
                    "Year": 2025,
                }
            ]
        )
        assert_frame_equal(actual.reset_index(drop=True), expected)

    @patch("src.ib.get_exchange_rate")
    def test_generate_aggregates_trade_and_cash(
        self,
        get_exchange_rate_mock,
    ) -> None:
        """Test generate combines trade and cash values into tax record."""
        reporter = self._reporter()
        get_exchange_rate_mock.return_value = 4.0
        entries = [(self._sample_trades(), self._sample_cash())]

        with patch.object(
            reporter,
            "_iter_statement_entries",
            return_value=iter(entries),
        ):
            report = reporter.generate()
        self.assertEqual(
            report,
            TaxReport(
                {
                    2025: TaxRecord(
                        trade_revenue=4872.0,
                        trade_cost=4008.0,
                        foreign_interest=120.0,
                        foreign_interest_withholding_tax=12.0,
                    ),
                    2026: TaxRecord(
                        trade_revenue=0.0,
                        trade_cost=0.0,
                        foreign_interest=0.0,
                        foreign_interest_withholding_tax=0.0,
                    ),
                }
            ),
        )


def test_generate_returns_empty_report_without_entries() -> None:
    """Test generate returns empty report when no statement entries."""
    reporter = IBTradeCashTaxReporter("query-id", "token")
    with patch.object(
        reporter,
        "_iter_statement_entries",
        return_value=iter([]),
    ):
        report = reporter.generate()
    assert not report.items()
