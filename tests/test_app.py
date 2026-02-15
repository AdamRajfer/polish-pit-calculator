"""Tests for console app helper functions and control flow."""

import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call, patch

import pytest
from prompt_toolkit.key_binding import KeyBindings

from src import app
from src.config import TaxRecord, TaxReport, TaxReporter


def _call_module(name: str, *args: Any, **kwargs: Any) -> Any:
    """Call private module-level helpers by name."""
    return getattr(app, name)(*args, **kwargs)


def _call_method(instance: object, name: str, *args: Any, **kwargs: Any) -> Any:
    """Call private instance methods by name."""
    return getattr(instance, name)(*args, **kwargs)


class DummyQuestion:
    """Question-like object used to test prompt wrappers."""

    def __init__(self, result: object) -> None:
        self.application = SimpleNamespace(
            ttimeoutlen=1,
            timeoutlen=1,
            key_bindings=KeyBindings(),
        )
        self._result = result

    def unsafe_ask(self) -> object:
        """Return configured answer payload."""
        return self._result

    def peek_result(self) -> object:
        """Expose payload for assertions."""
        return self._result


class DummyFileReporter(TaxReporter):
    """Minimal file-based reporter test double."""

    init_calls: list[tuple[object, ...]] = []

    def __init__(self, *args: object) -> None:
        self.args = args
        DummyFileReporter.init_calls.append(args)

    @classmethod
    def clear_calls(cls) -> None:
        """Reset recorded constructor calls."""
        cls.init_calls = []

    def generate(self) -> TaxReport:
        """Return fixed single-year report payload."""
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=10.0)
        return report


class DummyApiReporter(TaxReporter):
    """Minimal API-based reporter test double."""

    init_calls: list[tuple[object, ...]] = []

    def __init__(self, *args: object) -> None:
        self.args = args
        DummyApiReporter.init_calls.append(args)

    @classmethod
    def clear_calls(cls) -> None:
        """Reset recorded constructor calls."""
        cls.init_calls = []

    def generate(self) -> TaxReport:
        """Return fixed single-year report payload."""
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=5.0)
        return report


def _reset_dummy_calls() -> None:
    """Clear constructor-call history of dummy reporters."""
    DummyFileReporter.clear_calls()
    DummyApiReporter.clear_calls()


def test_ask_sets_timeouts_and_returns_result() -> None:
    """Test ask wrapper resets timeout values and returns prompt result."""
    question = DummyQuestion("ok")
    assert question.peek_result() == "ok"
    result = _call_module("_ask", question)
    assert result == "ok"
    assert question.application.ttimeoutlen == 0
    assert question.application.timeoutlen == 0


def test_bind_escape_back_rebinds_key_bindings() -> None:
    """Test escape binding wrapper augments question key bindings."""
    question = DummyQuestion("ok")
    original = question.application.key_bindings
    bound = _call_module("_bind_escape_back", question)
    assert bound is question
    assert bound.application.key_bindings is not original
    event = SimpleNamespace(app=SimpleNamespace(exit=Mock()))
    escape_binding = next(
        binding
        for binding in bound.application.key_bindings.bindings
        if binding.keys == ("escape",)
    )
    escape_binding.handler(event)
    event.app.exit.assert_called_once_with(result="__back__")


def test_clip_middle() -> None:
    """Test middle clipping behavior for long and short strings."""
    assert _call_module("_clip", "abcdefghij", 7) == "ab...ij"
    assert _call_module("_clip", "abcde", 4) == "a..."
    assert _call_module("_clip", "abc", 3) == "abc"
    assert _call_module("_clip", "abcdef", 2) == "ab"


def test_clear_last_lines_writes_escape_sequences() -> None:
    """Test terminal-clear helper emits expected escape sequence calls."""
    with patch.object(app.sys, "stdout") as stdout:
        _call_module("_clear_last_lines", 2)
    assert stdout.write.call_args_list == [
        call("\x1b[1A\x1b[2K"),
        call("\x1b[1A\x1b[2K"),
        call("\r"),
    ]
    stdout.flush.assert_called_once()


def test_clear_last_lines_noop_for_zero() -> None:
    """Test clear helper does nothing for non-positive values."""
    with patch.object(app.sys, "stdout") as stdout:
        _call_module("_clear_last_lines", 0)
    stdout.write.assert_not_called()
    stdout.flush.assert_not_called()


def test_disable_tty_input_echo_non_tty() -> None:
    """Test context manager no-ops for non-TTY stdin."""
    with patch.object(app.sys.stdin, "fileno", return_value=0):
        with patch.object(app.os, "isatty", return_value=False):
            with patch.object(app.termios, "tcgetattr") as tcgetattr:
                with _call_module("_disable_tty_input_echo"):
                    pass
    tcgetattr.assert_not_called()


def test_disable_tty_input_echo_tty_mode() -> None:
    """Test context manager applies and restores termios settings."""
    old = [0, 0, 0, app.termios.ECHO | app.termios.ICANON, 0, 0, 0]
    new = old.copy()
    with patch.object(app.os, "isatty", return_value=True):
        with patch.object(app.sys.stdin, "fileno", return_value=0):
            with patch.object(app.termios, "tcgetattr", side_effect=[old.copy(), new.copy()]):
                with patch.object(app.termios, "tcsetattr") as tcsetattr:
                    with patch.object(app.termios, "tcflush") as tcflush:
                        with _call_module("_disable_tty_input_echo"):
                            pass
    assert tcsetattr.call_count == 2
    assert tcflush.call_count == 2


def test_run_prepare_animation_renders_and_clears_line() -> None:
    """Test loader renders at least one frame and clears on stop."""

    class _StopAfterOne:
        def __init__(self) -> None:
            """Initialize loop-call counter."""
            self.calls = 0

        def is_set(self) -> bool:
            """Return True after first animation iteration."""
            self.calls += 1
            return self.calls > 1

    stop = _StopAfterOne()
    with patch.object(app, "time") as time_mod:
        time_mod.sleep.return_value = None
        with patch.object(app.sys, "stdout") as stdout:
            _call_module("_run_prepare_animation", stop)
    writes = [str(call_.args[0]) for call_ in stdout.write.call_args_list]
    assert any("Preparing tax summary" in value for value in writes)
    assert any("\x1b[2K" in value for value in writes)


def test_submission_table_line_count() -> None:
    """Test line count used for clearing printed submission table."""
    app_instance = app.PolishPitConsoleApp()
    assert _call_method(app_instance, "_submission_table_line_count") == 0
    app_instance.entries.extend(
        [
            {
                "tax_report_key": "k1",
                "report_title": "t1",
                "report_kind": "api",
                "report_cls": DummyApiReporter,
                "tax_report_data": {"query_id": "1", "token": "x"},
            },
            {
                "tax_report_key": "k2",
                "report_title": "t2",
                "report_kind": "api",
                "report_cls": DummyApiReporter,
                "tax_report_data": {"query_id": "2", "token": "x"},
            },
        ]
    )
    assert _call_method(app_instance, "_submission_table_line_count") == 6


def test_print_submission_line_first_row_prints_full_table() -> None:
    """Test first submission row prints complete table text."""
    app_instance = app.PolishPitConsoleApp()
    with patch.object(app, "tabulate", return_value="T1\nT2\nT3\nT4") as tab:
        with patch("builtins.print") as print_mock:
            _call_method(app_instance, "_print_submission_line", 1, "Title", "Details")
    tab.assert_called_once()
    print_mock.assert_called_once_with("T1\nT2\nT3\nT4", flush=True)


def test_print_submission_line_next_row_appends_only_tail() -> None:
    """Test subsequent submission rows append only data-table tail."""
    app_instance = app.PolishPitConsoleApp()
    table = "L1\nL2\nL3\nL4\nL5"
    with patch.object(app, "tabulate", return_value=table):
        with patch.object(app.sys, "stdout") as stdout:
            _call_method(app_instance, "_print_submission_line", 2, "Title", "Details")
    assert stdout.write.call_args_list == [call("\x1b[1A\x1b[2K\r"), call("L4\nL5\n")]
    stdout.flush.assert_called_once()


@patch("src.app.questionary.select")
def test_prompt_main_action_with_no_entries_disables_prepare(select: Mock) -> None:
    """Test prepare action is disabled when no reports are submitted."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(app, "_ask", return_value="submit"):
        action = _call_method(app_instance, "_prompt_main_action")
    assert action == "submit"
    choices = select.call_args.kwargs["choices"]
    assert choices[1].disabled == "Submit at least one tax report first"


@patch("src.app.questionary.select")
def test_prompt_main_action_pluralized_label(select: Mock) -> None:
    """Test prepare label pluralization and enabled state with entries."""
    app_instance = app.PolishPitConsoleApp()
    app_instance.entries.extend(
        [
            {
                "tax_report_key": "k1",
                "report_title": "t1",
                "report_kind": "api",
                "report_cls": DummyApiReporter,
                "tax_report_data": {"query_id": "1", "token": "x"},
            },
            {
                "tax_report_key": "k2",
                "report_title": "t2",
                "report_kind": "api",
                "report_cls": DummyApiReporter,
                "tax_report_data": {"query_id": "2", "token": "x"},
            },
        ]
    )
    select.return_value = object()
    with patch.object(app, "_ask", return_value="prepare"):
        action = _call_method(app_instance, "_prompt_main_action")
    assert action == "prepare"
    choices = select.call_args.kwargs["choices"]
    assert choices[1].title == "Prepare tax summary (2 tax reports)"
    assert choices[1].disabled is None


@patch("src.app.questionary.select")
def test_select_report_spec_back(select: Mock) -> None:
    """Test report selection returns back sentinel when escaped."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            result = _call_method(app_instance, "_select_report_spec")
    assert result == "__back__"


@patch("src.app.questionary.select")
def test_select_report_spec_returns_selected_spec(select: Mock) -> None:
    """Test report selector returns selected spec when not backing out."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    selected = ("ib_flex_query", "Interactive Brokers Flex Query", "api", DummyApiReporter)
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value=selected):
            result = _call_method(app_instance, "_select_report_spec")
    assert result == selected


@patch("src.app.questionary.text")
def test_collect_api_entry_back_on_query(text: Mock) -> None:
    """Test API entry collection aborts when back is chosen for query."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            result = _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    assert result is None


@patch("src.app.questionary.text")
def test_collect_api_entry_normalizes_query_and_token(text: Mock) -> None:
    """Test API entry normalizes query id and strips token input."""
    app_instance = app.PolishPitConsoleApp()
    text.side_effect = [object(), object()]
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", side_effect=["00123", " tok "]):
            entry = _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    assert entry is not None
    data = cast(app.ApiTaxReportData, entry["tax_report_data"])
    assert data["query_id"] == "123"
    assert data["token"] == "tok"


@patch("src.app.questionary.text")
def test_collect_api_entry_query_validation(text: Mock) -> None:
    """Test API query-id validator rejects empty and non-digit values."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    validate = text.call_args.kwargs["validate"]
    assert validate("") == "Query ID is required."
    assert validate("abc") == "Query ID must be an integer."
    assert validate("123") is True


@patch("src.app.questionary.text")
def test_collect_api_entry_token_back_returns_none(text: Mock) -> None:
    """Test API entry collection returns None when token prompt backs out."""
    app_instance = app.PolishPitConsoleApp()
    text.side_effect = [object(), object()]
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", side_effect=["123", "__back__"]):
            result = _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    assert result is None


@patch("src.app.questionary.text")
def test_collect_api_entry_token_validation(text: Mock) -> None:
    """Test API token validator rejects empty values."""
    app_instance = app.PolishPitConsoleApp()
    text.side_effect = [object(), object()]
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", side_effect=["123", "__back__"]):
            _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    validate = text.call_args_list[1].kwargs["validate"]
    assert validate("") == "This field is required."
    assert validate(" token ") is True


@patch("src.app.questionary.text")
def test_collect_file_entry_back(text: Mock) -> None:
    """Test file entry collection aborts when user goes back."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            result = _call_method(app_instance, "_collect_file_entry", "k", "t", DummyFileReporter)
    assert result is None


@patch("src.app.questionary.text")
def test_collect_file_entry_returns_resolved_path(text: Mock) -> None:
    """Test file entry collection stores absolute resolved path."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "file.csv"
        path.write_text("x", encoding="utf-8")
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", return_value=str(path)):
                entry = _call_method(
                    app_instance, "_collect_file_entry", "k", "t", DummyFileReporter
                )
    assert entry is not None
    assert entry["tax_report_data"] == path.resolve()


@patch("src.app.questionary.text")
def test_collect_file_entry_validation_rules(text: Mock) -> None:
    """Test file-entry validator checks required/existing/csv/duplicate."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            _call_method(app_instance, "_collect_file_entry", "k", "t", DummyFileReporter)
    validate = text.call_args.kwargs["validate"]
    assert validate("") == "This field is required."
    assert validate("/no/such/file.csv") == "Path must be a file."
    with tempfile.TemporaryDirectory() as tmp:
        txt_path = Path(tmp) / "x.txt"
        txt_path.write_text("x", encoding="utf-8")
        assert validate(str(txt_path)) == "Only .csv files are supported."
        csv_path = Path(tmp) / "x.csv"
        csv_path.write_text("x", encoding="utf-8")
        app_instance.entries = [
            {
                "tax_report_key": "k",
                "report_title": "t",
                "report_kind": "files",
                "report_cls": DummyFileReporter,
                "tax_report_data": csv_path.resolve(),
            }
        ]
        assert validate(str(csv_path)) == "File already submitted for this report type."
        app_instance.entries = []
        assert validate(str(csv_path)) is True
    file_filter = text.call_args.kwargs["completer"].file_filter
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        csv_path = tmp_dir / "y.csv"
        csv_path.write_text("x", encoding="utf-8")
        assert file_filter(str(tmp_dir)) is True
        assert file_filter(str(csv_path)) is True
        app_instance.entries = [
            {
                "tax_report_key": "k",
                "report_title": "t",
                "report_kind": "files",
                "report_cls": DummyFileReporter,
                "tax_report_data": csv_path.resolve(),
            }
        ]
        assert file_filter(str(csv_path)) is False


def test_collect_submission_entry_retries_after_back_from_inner() -> None:
    """Test submission collection retries after inner flow returns back."""
    app_instance = app.PolishPitConsoleApp()
    spec = ("k", "title", "files", DummyFileReporter)
    entry = {
        "tax_report_key": "k",
        "report_title": "title",
        "report_kind": "files",
        "report_cls": DummyFileReporter,
        "tax_report_data": Path("/tmp/x.csv"),
    }
    with patch.object(app_instance, "_select_report_spec", side_effect=[spec, spec]):
        with patch.object(app_instance, "_collect_file_entry", side_effect=[None, entry]):
            result = _call_method(app_instance, "_collect_submission_entry")
    assert result == entry


def test_collect_submission_entry_returns_none_on_top_level_back() -> None:
    """Test submission collection exits when report selector returns back."""
    app_instance = app.PolishPitConsoleApp()
    with patch.object(app_instance, "_select_report_spec", return_value="__back__"):
        result = _call_method(app_instance, "_collect_submission_entry")
    assert result is None


def test_collect_submission_entry_uses_api_collector() -> None:
    """Test API report spec routes to API collector method."""
    app_instance = app.PolishPitConsoleApp()
    spec = ("ib_flex_query", "Interactive Brokers Flex Query", "api", DummyApiReporter)
    entry = {
        "tax_report_key": "ib_flex_query",
        "report_title": "Interactive Brokers Flex Query",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "1", "token": "x"},
    }
    with patch.object(app_instance, "_select_report_spec", return_value=spec):
        with patch.object(app_instance, "_collect_api_entry", return_value=entry) as api_collect:
            result = _call_method(app_instance, "_collect_submission_entry")
    api_collect.assert_called_once()
    assert result == entry


def test_submission_details() -> None:
    """Test submission details text for file and API entries."""
    app_instance = app.PolishPitConsoleApp()
    file_entry: app.TaxReportEntry = {
        "tax_report_key": "k",
        "report_title": "t",
        "report_kind": "files",
        "report_cls": DummyFileReporter,
        "tax_report_data": Path("/tmp/a.csv"),
    }
    api_entry: app.TaxReportEntry = {
        "tax_report_key": "k",
        "report_title": "t",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "7", "token": "x"},
    }
    assert _call_method(app_instance, "_submission_details", file_entry) == "File: a.csv"
    assert _call_method(app_instance, "_submission_details", api_entry) == "Query ID: 7"


def test_print_last_submission_delegates_to_print_submission_line() -> None:
    """Test last-submission renderer delegates with computed index/details."""
    app_instance = app.PolishPitConsoleApp()
    entry: app.TaxReportEntry = {
        "tax_report_key": "k",
        "report_title": "Title",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "7", "token": "x"},
    }
    app_instance.entries.append(entry)
    with patch.object(app_instance, "_print_submission_line") as print_line:
        _call_method(app_instance, "_print_last_submission", entry)
    print_line.assert_called_once_with(1, "Title", "Query ID: 7")


def test_clear_submission_table_uses_line_count() -> None:
    """Test table clear helper delegates to _clear_last_lines with count."""
    app_instance = app.PolishPitConsoleApp()
    app_instance.entries.append(
        {
            "tax_report_key": "k",
            "report_title": "t",
            "report_kind": "api",
            "report_cls": DummyApiReporter,
            "tax_report_data": {"query_id": "1", "token": "x"},
        }
    )
    with patch.object(app, "_clear_last_lines") as clear_lines:
        _call_method(app_instance, "_clear_submission_table")
    clear_lines.assert_called_once_with(5)


def test_build_tax_report_aggregates_file_and_api() -> None:
    """Test report builder groups file entries and aggregates API entries."""
    _reset_dummy_calls()
    app_instance = app.PolishPitConsoleApp()
    with tempfile.TemporaryDirectory() as tmp:
        p1 = Path(tmp) / "a.csv"
        p2 = Path(tmp) / "b.csv"
        p1.write_text("1", encoding="utf-8")
        p2.write_text("2", encoding="utf-8")
        app_instance.entries = [
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
                "tax_report_data": {"query_id": "123", "token": "tok"},
            },
        ]
        report = _call_method(app_instance, "_build_tax_report")
    assert len(DummyFileReporter.init_calls) == 1
    assert len(DummyFileReporter.init_calls[0]) == 2
    assert DummyApiReporter.init_calls == [("123", "tok")]
    assert report[2025].trade_revenue == 15.0


def test_print_tax_summary_formats_values() -> None:
    """Test tax summary renderer formats numeric values with separators."""
    app_instance = app.PolishPitConsoleApp()
    report = TaxReport()
    report[2022] = TaxRecord(trade_revenue=1000.0)
    with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
        lines = _call_method(app_instance, "_print_tax_summary", report)
        text = out.getvalue()
    assert "1,000.00" in text
    assert "2022" in text
    assert lines > 0


def test_build_tax_report_with_loader_starts_and_joins_thread() -> None:
    """Test loader thread lifecycle while building tax report."""
    app_instance = app.PolishPitConsoleApp()
    report = TaxReport()
    fake_thread = Mock()
    fake_thread.start = Mock()
    fake_thread.join = Mock()
    with patch.object(app, "_disable_tty_input_echo") as context_manager:
        context_manager.return_value.__enter__.return_value = None
        context_manager.return_value.__exit__.return_value = None
        with patch.object(app.PolishPitConsoleApp, "_build_tax_report", return_value=report):
            with patch.object(app.threading, "Thread", return_value=fake_thread):
                got = _call_method(app_instance, "_build_tax_report_with_loader")
    assert got is report
    fake_thread.start.assert_called_once()
    fake_thread.join.assert_called_once()


def test_run_submit_prepare_start_over_then_exit() -> None:
    """Test end-to-end submit, prepare, restart and exit flow."""
    entry = {
        "tax_report_key": "api_key",
        "report_title": "Interactive Brokers Flex Query",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "7", "token": "x"},
    }
    with patch.object(
        app.PolishPitConsoleApp,
        "_prompt_main_action",
        side_effect=["submit", "prepare", "exit"],
    ):
        with patch.object(app.PolishPitConsoleApp, "_collect_submission_entry", return_value=entry):
            with patch.object(
                app.PolishPitConsoleApp, "_print_last_submission"
            ) as print_submission:
                with patch.object(
                    app.PolishPitConsoleApp,
                    "_build_tax_report_with_loader",
                    return_value=TaxReport(),
                ):
                    with patch.object(app.PolishPitConsoleApp, "_clear_submission_table"):
                        with patch.object(
                            app.PolishPitConsoleApp, "_print_tax_summary", return_value=9
                        ):
                            with patch.object(
                                app.PolishPitConsoleApp,
                                "_prompt_post_summary_action",
                                return_value="start_over",
                            ):
                                with patch.object(app, "_clear_last_lines") as clear_lines:
                                    with patch.object(app.sys, "exit", side_effect=SystemExit):
                                        with pytest.raises(SystemExit):
                                            app.PolishPitConsoleApp().run()
    print_submission.assert_called_once()
    clear_lines.assert_called_once_with(9)


def test_run_submit_none_then_exit() -> None:
    """Test submit path continues loop when entry collection returns None."""
    with patch.object(
        app.PolishPitConsoleApp,
        "_prompt_main_action",
        side_effect=["submit", "exit"],
    ):
        with patch.object(
            app.PolishPitConsoleApp,
            "_collect_submission_entry",
            return_value=None,
        ) as collect_entry:
            with patch.object(app.sys, "exit", side_effect=SystemExit):
                with pytest.raises(SystemExit):
                    app.PolishPitConsoleApp().run()
    assert collect_entry.call_count == 1


def test_run_unknown_action_loops_to_next_iteration() -> None:
    """Test run loop continues when action is outside expected literals."""
    with patch.object(
        app.PolishPitConsoleApp,
        "_prompt_main_action",
        side_effect=[cast(app.MenuAction, "unknown"), "exit"],
    ):
        with patch.object(app.sys, "exit", side_effect=SystemExit):
            with pytest.raises(SystemExit):
                app.PolishPitConsoleApp().run()


def test_run_prepare_exit_path_calls_sys_exit() -> None:
    """Test prepare path exits when post-summary action is exit."""
    entry: app.TaxReportEntry = {
        "tax_report_key": "api_key",
        "report_title": "Interactive Brokers Flex Query",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "7", "token": "x"},
    }
    with patch.object(
        app.PolishPitConsoleApp,
        "_prompt_main_action",
        side_effect=["prepare"],
    ):
        with patch.object(
            app.PolishPitConsoleApp, "_build_tax_report_with_loader", return_value=TaxReport()
        ):
            with patch.object(app.PolishPitConsoleApp, "_clear_submission_table"):
                with patch.object(app.PolishPitConsoleApp, "_print_tax_summary", return_value=1):
                    with patch.object(
                        app.PolishPitConsoleApp,
                        "_prompt_post_summary_action",
                        return_value="exit",
                    ):
                        with patch.object(app.sys, "exit", side_effect=SystemExit) as exit_mock:
                            app_instance = app.PolishPitConsoleApp()
                            app_instance.entries.append(entry)
                            with pytest.raises(SystemExit):
                                app_instance.run()
    exit_mock.assert_called_once_with(0)


@patch("src.app.questionary.select")
def test_prompt_post_summary_action_returns_value(select: Mock) -> None:
    """Test post-summary prompt returns selected action value."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(app, "_ask", return_value="exit"):
        action = _call_method(app_instance, "_prompt_post_summary_action")
    assert action == "exit"


def test_main_keyboard_interrupt_exits_zero() -> None:
    """Test main entrypoint exits with code 0 on keyboard interrupt."""
    with patch.object(app.PolishPitConsoleApp, "run", side_effect=KeyboardInterrupt):
        with patch.object(app.PolishPitConsoleApp, "reset") as reset_mock:
            with patch.object(app.sys, "exit", side_effect=SystemExit) as exit_mock:
                with pytest.raises(SystemExit):
                    app.main()
    reset_mock.assert_called_once()
    exit_mock.assert_called_once_with(0)
