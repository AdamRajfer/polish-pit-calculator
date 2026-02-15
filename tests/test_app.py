import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call, patch

from prompt_toolkit.key_binding import KeyBindings

from src import app
from src.config import TaxRecord, TaxReport, TaxReporter


class DummyQuestion:
    def __init__(self, result: object) -> None:
        self.application = SimpleNamespace(
            ttimeoutlen=1,
            timeoutlen=1,
            key_bindings=KeyBindings(),
        )
        self._result = result

    def unsafe_ask(self) -> object:
        return self._result


class DummyFileReporter(TaxReporter):
    init_calls: list[tuple[object, ...]] = []

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        DummyFileReporter.init_calls.append(args)

    def generate(self) -> TaxReport:
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=10.0)
        return report


class DummyApiReporter(TaxReporter):
    init_calls: list[tuple[object, ...]] = []

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        DummyApiReporter.init_calls.append(args)

    def generate(self) -> TaxReport:
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=5.0)
        return report


class TestApp(TestCase):
    def setUp(self) -> None:
        DummyFileReporter.init_calls = []
        DummyApiReporter.init_calls = []

    def test_ask_sets_timeouts_and_returns_result(self) -> None:
        question = DummyQuestion("ok")
        result = app._ask(question)  # type: ignore[arg-type]
        self.assertEqual(result, "ok")
        self.assertEqual(question.application.ttimeoutlen, 0)
        self.assertEqual(question.application.timeoutlen, 0)

    def test_bind_escape_back_rebinds_key_bindings(self) -> None:
        question = DummyQuestion("ok")
        original = question.application.key_bindings
        bound = app._bind_escape_back(question)  # type: ignore[arg-type]
        self.assertIs(bound, question)
        self.assertIsNot(bound.application.key_bindings, original)

    def test_clip_middle(self) -> None:
        self.assertEqual(app._clip("abcdefghij", 7), "ab...ij")
        self.assertEqual(app._clip("abc", 3), "abc")
        self.assertEqual(app._clip("abcdef", 2), "ab")

    def test_submission_table_line_count(self) -> None:
        self.assertEqual(app._submission_table_line_count(0), 0)
        self.assertEqual(app._submission_table_line_count(1), 5)
        self.assertEqual(app._submission_table_line_count(4), 8)

    def test_clear_last_lines_writes_escape_sequences(self) -> None:
        with patch.object(app.sys, "stdout") as stdout:
            app._clear_last_lines(2)
        self.assertEqual(
            stdout.write.call_args_list,
            [call("\x1b[1A\x1b[2K"), call("\x1b[1A\x1b[2K"), call("\r")],
        )
        stdout.flush.assert_called_once()

    def test_print_submission_line_first_row_prints_full_table(self) -> None:
        with patch.object(app, "tabulate", return_value="T1\nT2\nT3\nT4") as tab:
            with patch("builtins.print") as p:
                app._print_submission_line(1, "Title", "Details")
        tab.assert_called_once()
        p.assert_called_once_with("T1\nT2\nT3\nT4", flush=True)

    def test_print_submission_line_next_row_appends_only_tail(self) -> None:
        table = "L1\nL2\nL3\nL4\nL5"
        with patch.object(app, "tabulate", return_value=table):
            with patch.object(app.sys, "stdout") as stdout:
                app._print_submission_line(2, "Title", "Details")
        self.assertEqual(
            stdout.write.call_args_list,
            [call("\x1b[1A\x1b[2K\r"), call("L4\nL5\n")],
        )
        stdout.flush.assert_called_once()

    @patch("src.app.questionary.select")
    def test_prompt_main_action_with_no_entries_disables_prepare(
        self,
        select: Mock,
    ) -> None:
        select.return_value = object()
        with patch.object(app, "_ask", return_value="submit"):
            action = app._prompt_main_action(0)
        self.assertEqual(action, "submit")
        choices = select.call_args.kwargs["choices"]
        self.assertEqual(choices[1].disabled, "Submit at least one tax report first")

    @patch("src.app.questionary.select")
    def test_prompt_main_action_pluralized_label(self, select: Mock) -> None:
        select.return_value = object()
        with patch.object(app, "_ask", return_value="prepare"):
            action = app._prompt_main_action(2)
        self.assertEqual(action, "prepare")
        choices = select.call_args.kwargs["choices"]
        self.assertEqual(choices[1].title, "Prepare tax summary (2 tax reports)")
        self.assertIsNone(choices[1].disabled)

    @patch("src.app.questionary.select")
    def test_select_report_spec_back(self, select: Mock) -> None:
        select.return_value = object()
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", return_value="__back__"):
                result = app._select_report_spec()
        self.assertEqual(result, "__back__")

    @patch("src.app.questionary.text")
    def test_collect_api_entry_back_on_query(self, text: Mock) -> None:
        text.return_value = object()
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", return_value="__back__"):
                result = app._collect_api_entry("k", "t", DummyApiReporter)
        self.assertIsNone(result)

    @patch("src.app.questionary.text")
    def test_collect_api_entry_normalizes_query_and_token(
        self,
        text: Mock,
    ) -> None:
        text.side_effect = [object(), object()]
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", side_effect=["00123", " tok "]):
                entry = app._collect_api_entry("k", "t", DummyApiReporter)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["tax_report_data"]["query_id"], "123")
        self.assertEqual(entry["tax_report_data"]["token"], "tok")

    @patch("src.app.questionary.text")
    def test_collect_file_entry_back(self, text: Mock) -> None:
        text.return_value = object()
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", return_value="__back__"):
                result = app._collect_file_entry(
                    "k",
                    "t",
                    DummyFileReporter,
                    [],
                )
        self.assertIsNone(result)

    @patch("src.app.questionary.text")
    def test_collect_file_entry_returns_resolved_path(self, text: Mock) -> None:
        text.return_value = object()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.csv"
            path.write_text("x", encoding="utf-8")
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value=str(path)):
                    entry = app._collect_file_entry(
                        "k",
                        "t",
                        DummyFileReporter,
                        [],
                    )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["tax_report_data"], path.resolve())

    def test_collect_submission_entry_retries_after_back_from_inner(self) -> None:
        spec = ("k", "title", "files", DummyFileReporter)
        entry = {
            "tax_report_key": "k",
            "report_title": "title",
            "report_kind": "files",
            "report_cls": DummyFileReporter,
            "tax_report_data": Path("/tmp/x.csv"),
        }
        with patch.object(app, "_select_report_spec", side_effect=[spec, spec]):
            with patch.object(
                app,
                "_collect_file_entry",
                side_effect=[None, entry],
            ):
                result = app._collect_submission_entry([])
        self.assertEqual(result, entry)

    def test_submission_details(self) -> None:
        file_entry = {
            "tax_report_key": "k",
            "report_title": "t",
            "report_kind": "files",
            "report_cls": DummyFileReporter,
            "tax_report_data": Path("/tmp/a.csv"),
        }
        api_entry = {
            "tax_report_key": "k",
            "report_title": "t",
            "report_kind": "api",
            "report_cls": DummyApiReporter,
            "tax_report_data": {"query_id": "7", "token": "x"},
        }
        self.assertEqual(app._submission_details(file_entry), "File: a.csv")
        self.assertEqual(app._submission_details(api_entry), "Query ID: 7")

    def test_build_tax_report_aggregates_file_and_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.csv"
            p2 = Path(tmp) / "b.csv"
            p1.write_text("1", encoding="utf-8")
            p2.write_text("2", encoding="utf-8")
            entries = [
                {
                    "tax_report_key": "file_key",
                    "report_title": "file",
                    "report_kind": "files",
                    "report_cls": DummyFileReporter,
                    "tax_report_data": p1,
                },
                {
                    "tax_report_key": "file_key",
                    "report_title": "file",
                    "report_kind": "files",
                    "report_cls": DummyFileReporter,
                    "tax_report_data": p2,
                },
                {
                    "tax_report_key": "api_key",
                    "report_title": "api",
                    "report_kind": "api",
                    "report_cls": DummyApiReporter,
                    "tax_report_data": {
                        "query_id": "123",
                        "token": "tok",
                    },
                },
            ]
            report = app._build_tax_report(entries)
        self.assertEqual(len(DummyFileReporter.init_calls), 1)
        self.assertEqual(len(DummyFileReporter.init_calls[0]), 2)
        self.assertEqual(DummyApiReporter.init_calls, [("123", "tok")])
        self.assertEqual(report[2025].trade_revenue, 15.0)

    def test_print_tax_summary_formats_values(self) -> None:
        report = TaxReport()
        report[2022] = TaxRecord(trade_revenue=1000.0)
        with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
            lines = app._print_tax_summary(report)
            text = out.getvalue()
        self.assertIn("1,000.00", text)
        self.assertIn("2022", text)
        self.assertGreater(lines, 0)

    def test_build_tax_report_with_loader_starts_and_joins_thread(self) -> None:
        report = TaxReport()
        fake_thread = Mock()
        fake_thread.start = Mock()
        fake_thread.join = Mock()
        with patch.object(app, "_disable_tty_input_echo") as cm:
            cm.return_value.__enter__.return_value = None
            cm.return_value.__exit__.return_value = None
            with patch.object(app, "_build_tax_report", return_value=report):
                with patch.object(
                    app.threading,
                    "Thread",
                    return_value=fake_thread,
                ):
                    got = app._build_tax_report_with_loader([])
        self.assertIs(got, report)
        fake_thread.start.assert_called_once()
        fake_thread.join.assert_called_once()

    def test_main_prepare_without_entries_then_exit(self) -> None:
        with patch.object(
            app,
            "_prompt_main_action",
            side_effect=["prepare", "exit"],
        ):
            with patch("builtins.print") as p:
                with patch.object(app.sys, "exit", side_effect=SystemExit):
                    with self.assertRaises(SystemExit):
                        app.main()
        p.assert_any_call("No submitted tax reports.")

    def test_main_submit_prepare_start_over_then_exit(self) -> None:
        entry = {
            "tax_report_key": "api_key",
            "report_title": "Interactive Brokers Flex Query",
            "report_kind": "api",
            "report_cls": DummyApiReporter,
            "tax_report_data": {"query_id": "7", "token": "x"},
        }
        with patch.object(
            app,
            "_prompt_main_action",
            side_effect=["submit", "prepare", "exit"],
        ):
            with patch.object(app, "_collect_submission_entry", return_value=entry):
                with patch.object(app, "_submission_details", return_value="D"):
                    with patch.object(app, "_print_submission_line") as ps:
                        with patch.object(
                            app,
                            "_build_tax_report_with_loader",
                            return_value=TaxReport(),
                        ):
                            with patch.object(
                                app,
                                "_submission_table_line_count",
                                return_value=5,
                            ):
                                with patch.object(
                                    app,
                                    "_print_tax_summary",
                                    return_value=9,
                                ):
                                    with patch.object(
                                        app,
                                        "_prompt_post_summary_action",
                                        return_value="start_over",
                                    ):
                                        with patch.object(
                                            app,
                                            "_clear_last_lines",
                                        ) as cl:
                                            with patch.object(
                                                app.sys,
                                                "exit",
                                                side_effect=SystemExit,
                                            ):
                                                with self.assertRaises(SystemExit):
                                                    app.main()
        ps.assert_called_once_with(1, entry["report_title"], "D")
        self.assertEqual(cl.call_args_list, [call(5), call(9)])

    def test_run_keyboard_interrupt_exits_zero(self) -> None:
        with patch.object(app, "main", side_effect=KeyboardInterrupt):
            with patch.object(app.sys, "exit", side_effect=SystemExit) as ex:
                with self.assertRaises(SystemExit):
                    app.run()
        ex.assert_called_once_with(0)
