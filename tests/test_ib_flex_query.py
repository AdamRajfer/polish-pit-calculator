from datetime import date, datetime
from unittest import TestCase
from unittest.mock import call, patch
from urllib.parse import parse_qs, urlparse

import pandas as pd

from polish_pit_calculator.ib_flex_query import IBFlexQueryTaxReporter


def _xml_attrs(attrs: dict[str, str]) -> str:
    return " ".join(f"{k}='{v}'" for k, v in attrs.items())


def _statement_xml(
    trades: list[dict[str, str]] | None = None,
    cash: list[dict[str, str]] | None = None,
) -> str:
    trades = trades or []
    cash = cash or []
    trade_rows = "".join(f"<Trade {_xml_attrs(row)}/>" for row in trades)
    cash_rows = "".join(
        f"<CashTransaction {_xml_attrs(row)}/>" for row in cash
    )
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


class TestIBFlexQueryTaxReporter(TestCase):
    def _reporter(self) -> IBFlexQueryTaxReporter:
        return IBFlexQueryTaxReporter("query-id", "token")

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

    def test_parse_statement_entries(self) -> None:
        reporter = self._reporter()
        xml = _statement_xml(
            trades=[{"symbol": "AAPL", "quantity": "1"}],
            cash=[{"type": "Dividends", "amount": "1"}],
        )

        trades, cash = reporter._parse_statement_entries(xml)

        self.assertEqual(trades, [{"symbol": "AAPL", "quantity": "1"}])
        self.assertEqual(cash, [{"type": "Dividends", "amount": "1"}])

    def test_parse_statement_entries_without_flex_statement(self) -> None:
        reporter = self._reporter()
        xml = (
            "<FlexQueryResponse><FlexStatements count='0'/>"
            "</FlexQueryResponse>"
        )

        trades, cash = reporter._parse_statement_entries(xml)

        self.assertEqual((trades, cash), ([], []))

    def test_send_request_with_retry_success(self) -> None:
        reporter = self._reporter()
        response = _send_response(
            status="Success",
            reference="REF-1",
            url="https://ibkr.example/GetStatement",
        )
        with patch.object(reporter, "_fetch_url", return_value=response):
            ref, stmt_url, empty = reporter._send_request_with_retry("x")

        self.assertEqual(
            (ref, stmt_url, empty),
            (
                "REF-1",
                "https://ibkr.example/GetStatement",
                False,
            ),
        )

    def test_send_request_with_retry_empty_marker(self) -> None:
        reporter = self._reporter()
        response = _send_response(status="Fail", error_code="1003")
        with patch.object(reporter, "_fetch_url", return_value=response):
            result = reporter._send_request_with_retry("x")

        self.assertEqual(result, (None, None, True))

    def test_send_request_with_retry_retries_1018_then_success(self) -> None:
        reporter = self._reporter()
        responses = [
            _send_response(status="Warn", error_code="1018"),
            _send_response(status="Warn", reference="REF-2"),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch(
                "polish_pit_calculator.ib_flex_query.time.sleep"
            ) as sleep:
                result = reporter._send_request_with_retry("x")

        self.assertEqual(result, ("REF-2", None, False))
        sleep.assert_called_once()

    def test_send_request_with_retry_rate_limited(self) -> None:
        reporter = self._reporter()
        responses = [
            _send_response(status="Warn", error_code="1018"),
            _send_response(status="Warn", error_code="1018"),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch("polish_pit_calculator.ib_flex_query.time.sleep"):
                with self.assertRaisesRegex(ValueError, "rate-limited"):
                    reporter._send_request_with_retry("x", retries=2)

    def test_send_request_with_retry_unknown_error_raises(self) -> None:
        reporter = self._reporter()
        response = _send_response(status="Fail", error_code="9999")
        with patch.object(reporter, "_fetch_url", return_value=response):
            with self.assertRaisesRegex(ValueError, "SendRequest failed"):
                reporter._send_request_with_retry("x")

    def test_fetch_statement_with_retry_returns_statement_payload(
        self,
    ) -> None:
        reporter = self._reporter()
        payload = _statement_xml(
            trades=[{"symbol": "AAPL", "quantity": "1"}],
            cash=[],
        )
        with patch.object(reporter, "_fetch_url", return_value=payload):
            result = reporter._fetch_statement_with_retry("x")

        self.assertEqual(result, payload)

    def test_fetch_statement_with_retry_warn_then_payload(self) -> None:
        reporter = self._reporter()
        responses = [
            _statement_response(status="Warn", error_code="1018"),
            _statement_xml(),
        ]
        with patch.object(reporter, "_fetch_url", side_effect=responses):
            with patch(
                "polish_pit_calculator.ib_flex_query.time.sleep"
            ) as sleep:
                result = reporter._fetch_statement_with_retry("x")

        self.assertEqual(result, responses[1])
        sleep.assert_called_once()

    def test_fetch_statement_with_retry_failure_raises(self) -> None:
        reporter = self._reporter()
        response = _statement_response(status="Fail", error_code="1020")
        with patch.object(reporter, "_fetch_url", return_value=response):
            with self.assertRaisesRegex(ValueError, "GetStatement failed"):
                reporter._fetch_statement_with_retry("x")

    def test_fetch_statement_xml_builds_send_and_get_urls(self) -> None:
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
                result = reporter._fetch_statement_xml(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        self.assertEqual(result, "<xml/>")
        send_url = send_req.call_args.args[0]
        send_query = parse_qs(urlparse(send_url).query)
        self.assertEqual(send_query["t"], ["token"])
        self.assertEqual(send_query["q"], ["query-id"])
        self.assertEqual(send_query["fd"], ["20250101"])
        self.assertEqual(send_query["td"], ["20251231"])

        get_url = get_stmt.call_args.args[0]
        get_query = parse_qs(urlparse(get_url).query)
        self.assertEqual(get_query["t"], ["token"])
        self.assertEqual(get_query["q"], ["REF-3"])
        self.assertEqual(get_query["v"], ["3"])

    def test_fetch_statement_xml_uses_default_get_url(self) -> None:
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
                reporter._fetch_statement_xml(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        get_url = get_stmt.call_args.args[0]
        self.assertTrue(get_url.startswith(reporter.DEFAULT_GET_STATEMENT_URL))

    def test_fetch_statement_xml_returns_empty_marker_without_get_call(
        self,
    ) -> None:
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
                result = reporter._fetch_statement_xml(
                    "query-id",
                    "token",
                    "20250101",
                    "20251231",
                )

        self.assertEqual(result, reporter.EMPTY_STATEMENT_XML)
        get_stmt.assert_not_called()

    def test_resolve_current_year_entries_walks_back_until_non_empty(
        self,
    ) -> None:
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
                entries = reporter._resolve_current_year_entries(
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

    def test_iter_statement_entries_stops_after_first_empty_post_data(
        self,
    ) -> None:
        reporter = self._reporter()
        current_entries: tuple[
            list[dict[str, str]],
            list[dict[str, str]],
        ] = ([{"id": "current"}], [])
        with patch("polish_pit_calculator.ib_flex_query.datetime") as dt_mock:
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
                        entries = list(reporter._iter_statement_entries())

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

    @patch("polish_pit_calculator.ib_flex_query.get_exchange_rate")
    @patch("polish_pit_calculator.ib_flex_query.fetch_exchange_rates")
    def test_build_trades_dataframe_fifo(
        self,
        fetch_exchange_rates_mock,
        get_exchange_rate_mock,
    ) -> None:
        reporter = self._reporter()
        fetch_exchange_rates_mock.return_value = {"USD": {}}
        get_exchange_rate_mock.return_value = 4.0

        df = reporter._build_trades_dataframe(self._sample_trades())

        self.assertIsNotNone(df)
        assert df is not None
        self.assertEqual(len(df), 2)
        self.assertTrue((df["Year"] == 2025).all())
        self.assertAlmostEqual(float(df["buy_price_pln"].sum()), 4008.0)
        self.assertAlmostEqual(float(df["sell_price_pln"].sum()), 4872.0)

    def test_build_trades_dataframe_invalid_rows(self) -> None:
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

        df = reporter._build_trades_dataframe(rows)

        self.assertIsNone(df)

    @patch("polish_pit_calculator.ib_flex_query.get_exchange_rate")
    @patch("polish_pit_calculator.ib_flex_query.fetch_exchange_rates")
    def test_build_cash_dataframe_merges_and_calculates_pln(
        self,
        fetch_exchange_rates_mock,
        get_exchange_rate_mock,
    ) -> None:
        reporter = self._reporter()
        fetch_exchange_rates_mock.return_value = {"USD": {}}
        get_exchange_rate_mock.return_value = 4.0

        df = reporter._build_cash_dataframe(self._sample_cash())

        self.assertIsNotNone(df)
        assert df is not None
        self.assertEqual(len(df), 2)
        self.assertEqual(set(df["Description"]), {"ACME CORP", "CASH BALANCE"})
        self.assertAlmostEqual(float(df["income_pln"].sum()), 120.0)
        self.assertAlmostEqual(float(df["withholding_pln"].sum()), 12.0)
        self.assertTrue((df["Amount_wtax"] >= 0.0).all())

    def test_merge_income_with_empty_withholding(self) -> None:
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
        wtax_df = pd.DataFrame(
            columns=["Currency", "Description", "Date", "Year", "Amount"]
        )

        result = reporter._merge_income_with_withholding(
            income_df,
            wtax_df,
            r"\s*\([^()]*\)\s*$",
            r"\s-\s?.*$",
            True,
        )

        self.assertEqual(float(result.iloc[0]["Amount_wtax"]), 0.0)
        self.assertTrue(pd.isna(result.iloc[0]["Year"]))

    @patch("polish_pit_calculator.ib_flex_query.get_exchange_rate")
    @patch("polish_pit_calculator.ib_flex_query.fetch_exchange_rates")
    def test_generate_aggregates_trade_and_cash(
        self,
        fetch_exchange_rates_mock,
        get_exchange_rate_mock,
    ) -> None:
        reporter = self._reporter()
        fetch_exchange_rates_mock.return_value = {"USD": {}}
        get_exchange_rate_mock.return_value = 4.0
        entries = [(self._sample_trades(), self._sample_cash())]

        with patch.object(
            reporter,
            "_iter_statement_entries",
            return_value=iter(entries),
        ):
            report = reporter.generate()

        year_2025 = report[2025]
        self.assertAlmostEqual(year_2025.trade_revenue, 4872.0)
        self.assertAlmostEqual(year_2025.trade_cost, 4008.0)
        self.assertAlmostEqual(year_2025.foreign_interest, 120.0)
        self.assertAlmostEqual(
            year_2025.foreign_interest_withholding_tax,
            12.0,
        )

    def test_generate_returns_empty_report_without_entries(self) -> None:
        reporter = self._reporter()
        with patch.object(
            reporter,
            "_iter_statement_entries",
            return_value=iter([]),
        ):
            report = reporter.generate()

        self.assertEqual(report.items(), [])
