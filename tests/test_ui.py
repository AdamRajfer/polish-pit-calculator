"""Focused unit tests for polish_pit_calculator.ui helpers."""

import io
from io import UnsupportedOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from prompt_toolkit.key_binding import KeyBindings

from polish_pit_calculator import ui
from polish_pit_calculator.config import PromptValidator, TaxRecord, TaxReport
from polish_pit_calculator.tax_reporters import FileTaxReporter, TaxReporter


def _build_question(result: object = "ok") -> SimpleNamespace:
    question = SimpleNamespace()
    question.application = SimpleNamespace(
        ttimeoutlen=11,
        timeoutlen=22,
        key_bindings=KeyBindings(),
    )
    question.unsafe_ask = Mock(return_value=result)
    return question


class UiDummyReporter(TaxReporter):
    """Minimal reporter for validator-builder tests."""

    def __init__(self, field_one: str, another_field: str) -> None:
        self.field_one = field_one
        self.another_field = another_field

    @classmethod
    def name(cls) -> str:
        return "UI Dummy"

    @classmethod
    def validators(cls) -> dict[str, PromptValidator]:
        return {
            "field_one": lambda raw: True,
            "another_field": lambda raw: True,
        }

    @property
    def details(self) -> str:
        return f"{self.field_one}/{self.another_field}"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        return TaxReport()

    def to_entry_data(self) -> dict[str, Any]:
        return {"field_one": self.field_one, "another_field": self.another_field}


class UiDummyCsvFileReporter(FileTaxReporter):
    """Minimal file-backed reporter for path completer prompt tests."""

    @classmethod
    def extension(cls) -> str:
        return ".csv"

    @classmethod
    def name(cls) -> str:
        return "UI Dummy File"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        return TaxReport()


def test_ask_returns_prompt_result_and_sets_timeouts_to_zero() -> None:
    """ask should return unsafe_ask value and override timeout settings."""
    question = _build_question("value")
    ask = getattr(ui, "_ask")
    assert ask(question) == "value"
    assert question.application.ttimeoutlen == 0
    assert question.application.timeoutlen == 0


def test_ask_registers_escape_key_handler_with_back_sentinel() -> None:
    """Escape binding added by ask should exit prompt with '__back__'."""
    question = _build_question()
    ask = getattr(ui, "_ask")
    ask(question)
    escape_binding = None
    for binding in question.application.key_bindings.bindings:
        keys = getattr(binding, "keys")
        first = str(getattr(keys[0], "value", keys[0]))
        if first == "escape":
            escape_binding = binding
            break
    assert escape_binding is not None
    event = SimpleNamespace(app=SimpleNamespace(exit=Mock()))
    escape_binding.handler(event)
    event.app.exit.assert_called_once_with(result="__back__")


def test_ask_can_disable_escape_back_binding() -> None:
    """ask should skip ESC->'__back__' binding when explicitly disabled."""
    question = _build_question()
    ask = getattr(ui, "_ask")
    ask(question, disable_escape_back=True)
    assert not any(
        str(getattr(getattr(binding, "keys")[0], "value", getattr(binding, "keys")[0])) == "escape"
        for binding in question.application.key_bindings.bindings
    )


def test_ask_can_block_typed_input_through_parameter() -> None:
    """ask should add readonly key bindings when block_typed_input=True."""
    question = _build_question()
    ask = getattr(ui, "_ask")
    ask(question, block_typed_input=True)
    assert any(
        str(getattr(getattr(binding, "keys")[0], "value", getattr(binding, "keys")[0])) == "c-m"
        for binding in question.application.key_bindings.bindings
    )


def test_disable_tty_input_echo_is_noop_for_non_tty_and_fileno_errors() -> None:
    """Context manager should quietly pass for unsupported stdin scenarios."""
    disable_tty_input_echo = getattr(ui, "_disable_tty_input_echo")
    with patch.object(ui.sys.stdin, "fileno", side_effect=UnsupportedOperation()):
        with disable_tty_input_echo():
            pass
    with patch.object(ui.sys.stdin, "fileno", return_value=4):
        with patch.object(ui.os, "isatty", return_value=False):
            with disable_tty_input_echo():
                pass


def test_disable_tty_input_echo_updates_and_restores_termios_flags() -> None:
    """TTY mode should disable echo/canonical then restore original settings."""
    old = [0, 0, 0, 0b11, 0, 0, 0]
    new = [0, 0, 0, 0b11, 0, 0, 0]
    disable_tty_input_echo = getattr(ui, "_disable_tty_input_echo")
    with patch.object(ui.sys.stdin, "fileno", return_value=5):
        with patch.object(ui.os, "isatty", return_value=True):
            with patch.object(ui.termios, "tcgetattr", side_effect=[old, new]):
                with patch.object(ui.termios, "tcsetattr") as tcsetattr:
                    with patch.object(ui.termios, "tcflush") as tcflush:
                        with disable_tty_input_echo():
                            pass
    assert tcsetattr.call_count == 2
    assert tcflush.call_count == 2


def test_instantiate_tax_reporter_from_questionary_returns_none_on_back() -> None:
    """Builder should stop and return None when prompt returns '__back__'."""
    question = _build_question()
    with patch.object(ui.questionary, "text", return_value=question):
        with patch.object(ui, "_ask", return_value="__back__"):
            result = ui.prompt_for_tax_reporter(UiDummyReporter)
    assert result is None


def test_instantiate_tax_reporter_from_questionary_collects_and_builds_reporter() -> None:
    """Builder should trim values, prompt each field and instantiate reporter."""
    with patch.object(
        ui.questionary, "text", side_effect=[_build_question(), _build_question()]
    ) as text:
        with patch.object(ui, "_ask", side_effect=[" value-1 ", " value-2 "]):
            reporter = ui.prompt_for_tax_reporter(UiDummyReporter)

    assert reporter is not None
    assert isinstance(reporter, UiDummyReporter)
    assert reporter.field_one == "value-1"
    assert reporter.another_field == "value-2"
    prompts = [str(call_.args[0]) for call_ in text.call_args_list]
    assert prompts == ["Field One [esc to back]:", "Another Field [esc to back]:"]


def test_prompt_for_tax_reporter_uses_path_completer_for_file_reporters(tmp_path: Path) -> None:
    """File reporter path prompts should include GreatUXPathCompleter filtering."""
    valid_path = tmp_path / "sample.csv"
    invalid_path = tmp_path / "sample.txt"
    valid_path.write_text("x", encoding="utf-8")
    invalid_path.write_text("x", encoding="utf-8")

    with patch.object(ui.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui.questionary, "text", return_value=_build_question()) as text:
            with patch.object(ui, "_ask", return_value=str(valid_path)):
                reporter = ui.prompt_for_tax_reporter(UiDummyCsvFileReporter)

    assert reporter is not None
    assert isinstance(reporter, UiDummyCsvFileReporter)
    assert reporter.path == valid_path.resolve()
    completer = text.call_args.kwargs.get("completer")
    assert completer is not None
    assert completer.file_filter(str(tmp_path)) is True
    assert completer.file_filter(str(valid_path)) is True
    assert completer.file_filter(str(invalid_path)) is False


def test_print_tax_report_prints_logs_and_table_text() -> None:
    """print_tax_report should print composed logs and tabulated tax report."""
    tax_report = TaxReport({2025: TaxRecord(trade_revenue=1234.5)})
    with patch.object(ui.sys, "stdout", new=io.StringIO()) as stdout:
        ui.print_tax_report(tax_report, logs=["log one", "log two"])
    print_output = stdout.getvalue()
    assert "log one" in print_output
    assert "log two" in print_output
    assert "Trade Revenue" in print_output
    assert "2025" in print_output
    assert "1,234.50" in print_output


def test_print_tax_report_preserves_input_log_order() -> None:
    """print_tax_report should keep provided log order unchanged."""
    tax_report = TaxReport({2025: TaxRecord(trade_revenue=1.0)})
    logs = ["second", "first"]
    with patch.object(ui.sys, "stdout", new=io.StringIO()) as stdout:
        ui.print_tax_report(tax_report, logs=logs)
    output = stdout.getvalue()
    assert output.find("second") < output.find("first")
