"""Comprehensive unit tests for app module helpers and command flow."""

# pylint: disable=protected-access

import contextlib
import io
from datetime import datetime
from io import UnsupportedOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, call, patch

import pytest
from prompt_toolkit.key_binding import KeyBindings

import polish_pit_calculator.ui as ui_module
from polish_pit_calculator import app, tax_reporters
from polish_pit_calculator.config import TaxRecord, TaxReport, TaxReportLogs
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters import (
    ApiTaxReporter,
    CharlesSchwabEmployeeSponsoredTaxReporter,
    CoinbaseTaxReporter,
    EmploymentTaxReporter,
    FileTaxReporter,
    IBKRTaxReporter,
    RevolutInterestTaxReporter,
    TaxReporter,
)
from polish_pit_calculator.tax_reporters import file as file_module


class DummyQuestion:
    """Question-like object compatible with app.ask."""

    def __init__(self, result: object = "ok") -> None:
        self.application = SimpleNamespace(
            ttimeoutlen=1,
            timeoutlen=1,
            key_bindings=KeyBindings(),
        )
        self._result = result

    def unsafe_ask(self) -> object:
        """Return configured answer."""
        return self._result


class DummyFileReporter(FileTaxReporter):
    """Simple file-backed reporter for entry-collector tests."""

    @classmethod
    def extension(cls) -> str:
        """Return accepted extension for this test reporter."""
        return ".csv"

    @classmethod
    def name(cls) -> str:
        """Return display name."""
        return "Dummy File"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return deterministic data."""
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=1.0)
        return report


class DummyApiReporter(ApiTaxReporter):
    """Simple API-backed reporter for entry-collector tests."""

    @classmethod
    def name(cls) -> str:
        """Return display name."""
        return "Dummy API"

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return deterministic data."""
        report = TaxReport()
        report[2025] = TaxRecord(trade_revenue=2.0)
        return report


class UnsupportedReporter(TaxReporter):
    """Reporter class unsupported by register() branch selection."""

    @classmethod
    def name(cls) -> str:
        """Return display name."""
        return "Unsupported"

    @classmethod
    def validators(cls):
        """Return deterministic validator map for unsupported reporter tests."""
        return {"value": lambda raw: True}

    @property
    def details(self) -> str:
        """Return reporter details."""
        return ""

    def to_entry_data(self) -> dict[str, Any]:
        """Return deterministic reporter payload for unsupported reporter tests."""
        return {}

    def generate(self, logs: list[str] | None = None) -> TaxReport:
        """Return empty report."""
        return TaxReport()


def _key(binding: object) -> str:
    keys = getattr(binding, "keys")
    return str(getattr(keys[0], "value", keys[0]))


def _entry(
    *,
    entry_id: str,
    key: str,
    title: str,
    details: str,
    data: dict[str, Any],
    registry_path: Path | None = None,
) -> tuple[str, TaxReporter]:
    """Build one `(entry_id, reporter)` tuple for app unit tests."""
    _ = title
    _ = details
    _ = registry_path
    reporter_cls_by_name: dict[str, type[TaxReporter]] = {
        DummyFileReporter.__name__: DummyFileReporter,
        DummyApiReporter.__name__: DummyApiReporter,
        CharlesSchwabEmployeeSponsoredTaxReporter.__name__: (
            CharlesSchwabEmployeeSponsoredTaxReporter
        ),
        IBKRTaxReporter.__name__: IBKRTaxReporter,
        CoinbaseTaxReporter.__name__: CoinbaseTaxReporter,
        RevolutInterestTaxReporter.__name__: RevolutInterestTaxReporter,
        EmploymentTaxReporter.__name__: EmploymentTaxReporter,
    }
    reporter_cls = reporter_cls_by_name[key]
    reporter: TaxReporter
    if issubclass(reporter_cls, FileTaxReporter):
        reporter = reporter_cls(data["path"])
    elif issubclass(reporter_cls, ApiTaxReporter):
        reporter = reporter_cls(data["query_id"], data["token"])
    elif reporter_cls is EmploymentTaxReporter:
        reporter = EmploymentTaxReporter(
            data["year"],
            data["employment_revenue"],
            data["employment_cost"],
            data["social_security_contributions"],
            data["donations"],
        )
    else:
        raise TypeError(f"Unsupported reporter class: {reporter_cls}")
    return entry_id, reporter


def _report(**record_kwargs: float) -> TaxReport:
    """Build one-year report helper."""
    report = TaxReport()
    report[2025] = TaxRecord(**record_kwargs)
    return report


def _fake_context() -> contextlib.AbstractContextManager[None]:
    """Return no-op context manager helper."""
    return contextlib.nullcontext()


def _fake_thread_noop(*_args: Any, **_kwargs: Any) -> Any:
    """Return thread-like object whose lifecycle methods are no-ops."""

    class _Thread:
        """No-op thread replacement for unit tests."""

        def start(self) -> None:
            """Start no-op thread."""
            return

        def join(self) -> None:
            """Join no-op thread."""
            return

    return _Thread()


def test_ask_sets_timeouts_and_returns_answer() -> None:
    """_ask should set prompt timeouts to 0 and return prompt value."""
    question = DummyQuestion("ok")
    result = ui_module._ask(question)
    assert result == "ok"
    assert question.application.ttimeoutlen == 0
    assert question.application.timeoutlen == 0


def test_ask_injects_escape_binding_that_returns_back() -> None:
    """Escape key handler should exit prompt with explicit back sentinel."""
    question = DummyQuestion("ok")
    ui_module._ask(question)

    escape_binding = next(
        binding
        for binding in question.application.key_bindings.bindings
        if _key(binding) == "escape"
    )
    event = SimpleNamespace(app=SimpleNamespace(exit=Mock()))
    escape_binding.handler(event)
    event.app.exit.assert_called_once_with(result="__back__")


def test_ask_readonly_mode_binds_enter_and_printable_keys() -> None:
    """Readonly prompts should ignore Enter and ASCII key presses."""
    question = DummyQuestion("ok")
    ui_module._ask(question, block_typed_input=True)

    assert any(_key(binding) == "c-m" for binding in question.application.key_bindings.bindings)
    assert any(_key(binding) == "0" for binding in question.application.key_bindings.bindings)
    assert any(_key(binding) == "9" for binding in question.application.key_bindings.bindings)
    assert any(_key(binding) == "A" for binding in question.application.key_bindings.bindings)
    assert any(_key(binding) == "z" for binding in question.application.key_bindings.bindings)

    enter_binding = next(
        binding for binding in question.application.key_bindings.bindings if _key(binding) == "c-m"
    )
    digit_binding = next(
        binding for binding in question.application.key_bindings.bindings if _key(binding) == "7"
    )
    enter_binding.handler(SimpleNamespace())
    digit_binding.handler(SimpleNamespace())


def testdisable_tty_input_echo_skips_on_unsupported_fileno() -> None:
    """Context manager should no-op for unsupported stdin.fileno."""
    with patch.object(ui_module.sys.stdin, "fileno", side_effect=UnsupportedOperation):
        with patch.object(ui_module.termios, "tcgetattr") as tcgetattr:
            with app.ui._disable_tty_input_echo():
                pass
    tcgetattr.assert_not_called()


def testdisable_tty_input_echo_skips_on_oserror_fileno() -> None:
    """Context manager should no-op for OSError from stdin.fileno."""
    with patch.object(ui_module.sys.stdin, "fileno", side_effect=OSError):
        with patch.object(ui_module.termios, "tcgetattr") as tcgetattr:
            with app.ui._disable_tty_input_echo():
                pass
    tcgetattr.assert_not_called()


def testdisable_tty_input_echo_skips_for_non_tty() -> None:
    """Context manager should no-op when stdin is not a TTY."""
    with patch.object(ui_module.sys.stdin, "fileno", return_value=0):
        with patch.object(ui_module.os, "isatty", return_value=False):
            with patch.object(ui_module.termios, "tcgetattr") as tcgetattr:
                with app.ui._disable_tty_input_echo():
                    pass
    tcgetattr.assert_not_called()


def testdisable_tty_input_echo_toggles_and_restores_terminal_flags() -> None:
    """TTY mode should disable echo/canonical input and restore old state."""
    old = [0, 0, 0, ui_module.termios.ECHO | ui_module.termios.ICANON, 0, 0, 0]
    new = old.copy()

    with patch.object(ui_module.sys.stdin, "fileno", return_value=0):
        with patch.object(ui_module.os, "isatty", return_value=True):
            with patch.object(ui_module.termios, "tcgetattr", side_effect=[old.copy(), new.copy()]):
                with patch.object(ui_module.termios, "tcsetattr") as tcsetattr:
                    with patch.object(ui_module.termios, "tcflush") as tcflush:
                        with app.ui._disable_tty_input_echo():
                            pass

    assert tcsetattr.call_count == 2
    assert tcflush.call_count == 2
    modified = tcsetattr.call_args_list[0].args[2]
    assert modified[3] & ui_module.termios.ECHO == 0
    assert modified[3] & ui_module.termios.ICANON == 0


def test_build_file_reporter_returns_none_on_back() -> None:
    """Validator-based builder should return None on first ESC/back."""
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()):
            with patch.object(ui_module, "_ask", return_value="__back__"):
                result = ui_module.prompt_for_tax_reporter(DummyFileReporter)
    assert result is None


def test_build_file_reporter_returns_path_payload_and_details(tmp_path: Path) -> None:
    """Validator-based builder should resolve path and build reporter instance."""
    path = tmp_path / "input.csv"
    path.write_text("x", encoding="utf-8")

    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()):
            with patch.object(ui_module, "_ask", return_value=str(path)):
                reporter = ui_module.prompt_for_tax_reporter(DummyFileReporter)

    assert reporter is not None
    assert isinstance(reporter, DummyFileReporter)
    assert reporter.path == path.resolve()
    assert reporter.details == f"File: {path.name}"


def test_build_file_reporter_validation_rejects_blank_and_missing_path() -> None:
    """File-input validation should reject blank and missing file paths."""
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
            with patch.object(ui_module, "_ask", return_value="__back__"):
                ui_module.prompt_for_tax_reporter(DummyFileReporter)

    validate = text.call_args.kwargs["validate"]
    assert validate("") == "This field is required."
    assert validate("/does/not/exist.csv") == "Path must be a file."


def test_build_file_reporter_validation_uses_reporter_specific_rule(tmp_path: Path) -> None:
    """File-input validation should use reporter-specific extension rule."""
    txt = tmp_path / "input.txt"
    txt.write_text("x", encoding="utf-8")

    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
            with patch.object(ui_module, "_ask", return_value="__back__"):
                ui_module.prompt_for_tax_reporter(DummyFileReporter)

    validate = text.call_args.kwargs["validate"]
    assert validate(str(txt)) == "Only .csv files are supported."


def test_build_file_reporter_validation_rejects_duplicate_for_same_reporter(tmp_path: Path) -> None:
    """File-input validation should reject already registered paths for same reporter key."""
    path = tmp_path / "registered.csv"
    path.write_text("x", encoding="utf-8")

    entries = [
        _entry(
            entry_id="123456789",
            key=DummyFileReporter.__name__,
            title="Dummy",
            details="File",
            data={"path": str(path.resolve())},
        )
    ]
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
            with patch.object(ui_module, "_ask", return_value="__back__"):
                ui_module.prompt_for_tax_reporter(DummyFileReporter)

    validate = text.call_args.kwargs["validate"]
    assert validate(str(path.resolve())) == "File already registered for this report type."


def test_build_file_reporter_validation_allows_same_path_for_other_reporter(tmp_path: Path) -> None:
    """Duplicate-path protection should be scoped to selected reporter type only."""
    path = tmp_path / "shared.csv"
    path.write_text("x", encoding="utf-8")

    with patch.object(
        file_module.TaxReporterRegistry, "deserialize_all", return_value=[]
    ) as read_entries:
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
            with patch.object(ui_module, "_ask", return_value="__back__"):
                ui_module.prompt_for_tax_reporter(DummyFileReporter)

    read_entries.assert_called_once_with(DummyFileReporter)
    validate = text.call_args.kwargs["validate"]
    assert validate(str(path.resolve())) is True


def test_build_file_reporter_prompt_label_is_derived_from_attribute_name() -> None:
    """Validator-based builder should derive prompt label from constructor attribute."""
    with patch.object(file_module.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
            with patch.object(ui_module, "_ask", return_value="__back__"):
                ui_module.prompt_for_tax_reporter(DummyFileReporter)
    assert text.call_args.args[0] == "Path [esc to back]:"


def test_build_api_reporter_returns_none_on_back_at_query_prompt() -> None:
    """API reporter builder should return None when query-id prompt is cancelled."""
    with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()):
        with patch.object(ui_module, "_ask", return_value="__back__"):
            result = ui_module.prompt_for_tax_reporter(DummyApiReporter)
    assert result is None


def test_build_api_reporter_returns_none_on_back_at_token_prompt() -> None:
    """API reporter builder should return None when token prompt is cancelled."""
    with patch.object(
        ui_module.questionary, "text", side_effect=[DummyQuestion(), DummyQuestion()]
    ):
        with patch.object(ui_module, "_ask", side_effect=["123", "__back__"]):
            result = ui_module.prompt_for_tax_reporter(DummyApiReporter)
    assert result is None


def test_build_api_reporter_trims_values_and_builds_payload() -> None:
    """API reporter builder should trim query-id/token and build reporter instance."""
    with patch.object(
        ui_module.questionary, "text", side_effect=[DummyQuestion(), DummyQuestion()]
    ):
        with patch.object(ui_module, "_ask", side_effect=[" 00123 ", " tok "]):
            reporter = ui_module.prompt_for_tax_reporter(DummyApiReporter)

    assert reporter is not None
    assert isinstance(reporter, DummyApiReporter)
    assert reporter.query_id == "00123"
    assert reporter.token == "tok"
    assert reporter.details == "Query ID: 00123"


def test_build_api_reporter_query_prompt_uses_reporter_validator() -> None:
    """API query prompt should use base non-empty validator callback."""
    with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
        with patch.object(ui_module, "_ask", return_value="__back__"):
            ui_module.prompt_for_tax_reporter(DummyApiReporter)

    validate = text.call_args.kwargs["validate"]
    assert validate("") == "Query ID is required."
    assert validate("abc") is True
    assert validate("123") is True


def test_build_api_reporter_token_prompt_uses_reporter_validator() -> None:
    """API token prompt should use reporter _validate_token callback."""
    with patch.object(
        ui_module.questionary, "text", side_effect=[DummyQuestion(), DummyQuestion()]
    ) as text:
        with patch.object(ui_module, "_ask", side_effect=["1", "__back__"]):
            ui_module.prompt_for_tax_reporter(DummyApiReporter)

    validate = text.call_args_list[1].kwargs["validate"]
    assert validate("") == "Token is required."
    assert validate(" token ") is True


def test_build_employment_reporter_returns_none_on_first_back() -> None:
    """Employment builder should return None when year prompt is cancelled."""
    with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()):
        with patch.object(ui_module, "_ask", return_value="__back__"):
            result = ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)
    assert result is None


def test_build_employment_reporter_returns_none_on_midway_back() -> None:
    """Employment builder should return None when later prompt is cancelled."""
    with patch.object(ui_module.questionary, "text", side_effect=[DummyQuestion()] * 5):
        with patch.object(ui_module, "_ask", side_effect=["2025", "10", "20", "__back__"]):
            result = ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)
    assert result is None


def test_build_employment_reporter_parses_year_and_amounts() -> None:
    """Employment builder should cast values to expected numeric types."""
    with patch.object(ui_module.questionary, "text", side_effect=[DummyQuestion()] * 5):
        with patch.object(ui_module, "_ask", side_effect=["2025", "1", "2.2", "3.3", "4"]):
            reporter = ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)

    assert reporter is not None
    assert isinstance(reporter, EmploymentTaxReporter)
    assert reporter.year == 2025
    assert reporter.employment_revenue == 1.0
    assert reporter.employment_cost == 2.2
    assert reporter.social_security_contributions == 3.3
    assert reporter.donations == 4.0
    assert reporter.details == (
        "Year: 2025 "
        "Employment Revenue: 1.00 "
        "Employment Cost: 2.20 "
        "Social Security Contributions: 3.30 "
        "Donations: 4.00"
    )


def test_build_employment_reporter_year_validator_rules() -> None:
    """Year validator should reject blank/non-integer and accept integer values."""
    with patch.object(ui_module.questionary, "text", return_value=DummyQuestion()) as text:
        with patch.object(ui_module, "_ask", return_value="__back__"):
            ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)

    validate = text.call_args.kwargs["validate"]
    assert validate("") == "Year is required."
    assert validate("abc") == "Year must be an integer."
    assert validate("2025") is True


def test_build_employment_reporter_amount_validator_rules() -> None:
    """Amount validator should reject blank/non-numeric and accept numeric values."""
    with patch.object(
        ui_module.questionary, "text", side_effect=[DummyQuestion(), DummyQuestion()]
    ) as text:
        with patch.object(ui_module, "_ask", side_effect=["2025", "__back__"]):
            ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)

    validate = text.call_args_list[1].kwargs["validate"]
    assert validate("") == "Amount is required."
    assert validate("abc") == "Amount must be a number."
    assert validate("12") is True
    assert validate("12.5") is True


def test_build_employment_reporter_prompts_all_expected_labels() -> None:
    """Employment builder should prompt all required fields in expected order."""
    with patch.object(ui_module.questionary, "text", side_effect=[DummyQuestion()] * 5) as text:
        with patch.object(ui_module, "_ask", side_effect=["2025", "1", "2", "3", "4"]):
            ui_module.prompt_for_tax_reporter(EmploymentTaxReporter)

    prompts = [str(call_.args[0]) for call_ in text.call_args_list]
    assert prompts == [
        "Year [esc to back]:",
        "Employment Revenue [esc to back]:",
        "Employment Cost [esc to back]:",
        "Social Security Contributions [esc to back]:",
        "Donations [esc to back]:",
    ]


def test_run_dispatches_all_menu_actions_once() -> None:
    """run() should dispatch supported menu actions and stop on exit."""
    app_instance = app.App()
    with patch.object(
        app.ui,
        "prompt_for_main_menu_action",
        side_effect=["register", "ls", "rm", "report", "show", "exit_app"],
    ):
        with patch.object(app_instance, "register") as register:
            with patch.object(app_instance, "ls") as ls:
                with patch.object(app_instance, "rm") as rm:
                    with patch.object(app_instance, "report") as report:
                        with patch.object(app_instance, "show") as show:
                            with patch.object(app_instance, "exit_app") as exit_app:
                                exit_app.side_effect = SystemExit
                                with pytest.raises(SystemExit):
                                    app_instance.run()

    register.assert_called_once()
    ls.assert_called_once()
    rm.assert_called_once()
    report.assert_called_once()
    show.assert_called_once()
    exit_app.assert_called_once()


def test_run_clears_terminal_before_rendering_menu() -> None:
    """run() should clear viewport and scrollback before showing main menu."""
    app_instance = app.App()

    with patch.object(app.ui, "clear_terminal_viewport") as clear_terminal:
        with patch.object(app.ui, "prompt_for_main_menu_action", return_value="exit_app"):
            with patch.object(app_instance, "exit_app", side_effect=SystemExit):
                with pytest.raises(SystemExit):
                    app_instance.run()

    clear_terminal.assert_called_once_with()


def test_run_clears_terminal_on_each_menu_iteration() -> None:
    """run() should clear terminal at the start of each loop iteration."""
    app_instance = app.App()

    with patch.object(app.ui, "clear_terminal_viewport") as clear_terminal:
        with patch.object(
            app.ui, "prompt_for_main_menu_action", side_effect=["register", "exit_app"]
        ):
            with patch.object(app_instance, "register"):
                with patch.object(app_instance, "exit_app", side_effect=SystemExit):
                    with pytest.raises(SystemExit):
                        app_instance.run()

    assert clear_terminal.call_count == 2


def test_run_menu_disables_registry_actions_when_no_entries() -> None:
    """Main menu should disable list/rm/report actions when registry is empty."""
    app_instance = app.App()
    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()) as select:
            with patch.object(app.ui, "_ask", return_value="exit_app"):
                with patch.object(app_instance, "exit_app", side_effect=SystemExit):
                    with pytest.raises(SystemExit):
                        app_instance.run()

    choices = select.call_args.kwargs["choices"]
    disabled = {choice.title: choice.disabled for choice in choices}
    assert disabled["Register tax reporter"] is None
    assert disabled["List tax reporters"] == "No registered tax reporters"
    assert disabled["Remove tax reporters"] == "No registered tax reporters"
    assert disabled["Prepare tax report"] == "No registered tax reporters"
    assert disabled["Show tax report"] == "No prepared report in this session"
    assert disabled["Reset tax report"] == "No prepared report in this session"
    assert disabled["Exit"] is None


def test_run_menu_enables_show_and_reset_when_report_is_loaded() -> None:
    """Main menu should enable show/reset when in-session report exists."""
    app_instance = app.App()
    app_instance.tax_report = TaxReport()
    reporters = [("1", DummyFileReporter("/tmp/raw.csv"))]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=reporters):
        with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()) as select:
            with patch.object(app.ui, "_ask", return_value="exit_app"):
                with patch.object(app_instance, "exit_app", side_effect=SystemExit):
                    with pytest.raises(SystemExit):
                        app_instance.run()

    choices = select.call_args.kwargs["choices"]
    disabled = {choice.title: choice.disabled for choice in choices}
    assert disabled["Show tax report"] is None
    assert disabled["Reset tax report"] is None


def test_run_raises_for_unexpected_command() -> None:
    """run() should fail fast for unknown menu command values."""
    app_instance = app.App()
    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
            with patch.object(app.ui, "_ask", return_value="boom"):
                with pytest.raises(AttributeError, match="has no attribute 'boom'"):
                    app_instance.run()


def test_run_main_menu_disables_escape_back_binding() -> None:
    """run() should call ask() with disable_escape_back=True for main menu."""
    app_instance = app.App()
    question = DummyQuestion()
    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(app.ui.questionary, "select", return_value=question):
            with patch.object(app.ui, "_ask", return_value="exit_app") as ask:
                with patch.object(app_instance, "exit_app", side_effect=SystemExit):
                    with pytest.raises(SystemExit):
                        app_instance.run()

    ask.assert_called_once_with(question, disable_escape_back=True)


def test_register_returns_without_write_on_top_level_back() -> None:
    """register() should stop immediately when report-type selection is cancelled."""
    app_instance = app.App()

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value="__back__"):
            with patch.object(app_instance, "_reset") as reset:
                app_instance.register()

    reset.assert_not_called()


def test_register_file_reporter_flow_writes_entry_and_resets() -> None:
    """register() should collect file entry, write it and reset session state."""
    app_instance = app.App()
    reporter = DummyFileReporter("/tmp/raw.csv")

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value=DummyFileReporter):
            with patch.object(app.ui, "prompt_for_tax_reporter", return_value=reporter) as collect:
                with patch.object(
                    app.TaxReporterRegistry,
                    "serialize",
                    return_value="123456789",
                ) as serialize:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.register()

    collect.assert_called_once_with(DummyFileReporter)
    serialize.assert_called_once_with(reporter)
    reset.assert_called_once()


def test_register_api_reporter_flow_writes_entry_and_resets() -> None:
    """register() should collect API entry, write it and reset session state."""
    app_instance = app.App()
    reporter = IBKRTaxReporter("7", "x")

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value=IBKRTaxReporter):
            with patch.object(
                app.ui,
                "prompt_for_tax_reporter",
                return_value=reporter,
            ) as collect:
                with patch.object(
                    app.TaxReporterRegistry,
                    "serialize",
                    return_value="123456789",
                ) as serialize:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.register()

    collect.assert_called_once_with(IBKRTaxReporter)
    serialize.assert_called_once_with(reporter)
    reset.assert_called_once()


def test_register_employment_flow_writes_entry_and_resets() -> None:
    """register() should collect employment entry, write it and reset session state."""
    app_instance = app.App()
    reporter = EmploymentTaxReporter(2025, 1.0, 2.0, 3.0, 4.0)

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value=EmploymentTaxReporter):
            with patch.object(
                app.ui,
                "prompt_for_tax_reporter",
                return_value=reporter,
            ) as collect:
                with patch.object(
                    app.TaxReporterRegistry,
                    "serialize",
                    return_value="123456789",
                ) as serialize:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.register()

    collect.assert_called_once_with(EmploymentTaxReporter)
    serialize.assert_called_once_with(reporter)
    reset.assert_called_once()


def test_register_retries_when_inner_collector_returns_none() -> None:
    """register() should retry selection loop after inner collector returns None."""
    app_instance = app.App()
    reporter = DummyFileReporter("/tmp/raw.csv")

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", side_effect=[DummyFileReporter, DummyFileReporter]):
            with patch.object(
                app.ui,
                "prompt_for_tax_reporter",
                side_effect=[None, reporter],
            ) as collect:
                with patch.object(
                    app.TaxReporterRegistry,
                    "serialize",
                    return_value="123456789",
                ) as serialize:
                    app_instance.register()

    assert collect.call_args_list == [call(DummyFileReporter), call(DummyFileReporter)]
    serialize.assert_called_once_with(reporter)


def test_register_uses_selected_reporter_class_without_extra_type_guard() -> None:
    """register() should rely on selected class and persist returned reporter."""
    app_instance = app.App()
    reporter = UnsupportedReporter()

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value=UnsupportedReporter):
            with patch.object(
                app.ui,
                "prompt_for_tax_reporter",
                return_value=reporter,
            ) as collect:
                with patch.object(
                    app.TaxReporterRegistry,
                    "serialize",
                    return_value="123456789",
                ) as serialize:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.register()

    collect.assert_called_once_with(UnsupportedReporter)
    serialize.assert_called_once_with(reporter)
    reset.assert_called_once_with()


def test_register_reporter_choice_order_matches_expected_names() -> None:
    """register() prompt should use reporter registry choice ordering."""
    app_instance = app.App()

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()) as select:
        with patch.object(app.ui, "_ask", return_value="__back__"):
            app_instance.register()

    choices = select.call_args.kwargs["choices"]
    assert [choice.title for choice in choices] == [
        class_def.name() for class_def in app.TaxReporterRegistry.ls()
    ]


def test_register_integration_writes_registry_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """register() should write real encoded registry entry through caches layer."""
    monkeypatch.setenv(TaxReporterRegistry._dir_env_var_name, str(tmp_path / "registry"))
    monkeypatch.setattr(
        tax_reporters,
        DummyFileReporter.__name__,
        DummyFileReporter,
        raising=False,
    )

    app_instance = app.App()
    reporter = DummyFileReporter(tmp_path / "raw.csv")

    with patch.object(app.ui.questionary, "select", return_value=DummyQuestion()):
        with patch.object(app.ui, "_ask", return_value=DummyFileReporter):
            with patch.object(app.ui, "prompt_for_tax_reporter", return_value=reporter):
                app_instance.register()

    entries = TaxReporterRegistry.deserialize_all()
    assert len(entries) == 1
    assert entries[0][0].isdigit()
    assert isinstance(entries[0][1], DummyFileReporter)
    assert entries[0][1].path == (tmp_path / "raw.csv").resolve()


def test_ls_prints_table_and_waits_for_back() -> None:
    """ls() should print tabulated entries and wait for back prompt."""
    app_instance = app.App()
    question = DummyQuestion("__back__")
    entries = [
        _entry(
            entry_id="000000001",
            key="DummyFileReporter",
            title="Raw Custom CSV",
            details="File: raw.csv",
            data={"path": "/tmp/raw.csv"},
        ),
        _entry(
            entry_id="000000002",
            key="IBKRTaxReporter",
            title="Interactive Brokers",
            details="Query ID: 1",
            data={"query_id": "1", "token": "x"},
        ),
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui.questionary, "text", return_value=question) as text:
            with patch.object(app.ui, "_ask", return_value="__back__") as ask:
                with patch.object(app.sys, "stdout", new=io.StringIO()) as stdout:
                    app_instance.ls()

    output = stdout.getvalue()
    assert "ID" in output
    assert "Tax Reporter" in output
    assert "Dummy File" in output
    assert "Interactive Brokers" in output
    text.assert_called_once_with("[esc to back]", erase_when_done=True)
    ask.assert_called_once_with(question, block_typed_input=True)


def test_ls_handles_empty_registry_entries() -> None:
    """ls() should still render headers for empty registry."""
    app_instance = app.App()
    question = DummyQuestion("__back__")

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=[]):
        with patch.object(app.ui.questionary, "text", return_value=question):
            with patch.object(app.ui, "_ask", return_value="__back__"):
                with patch.object(app.sys, "stdout", new=io.StringIO()) as stdout:
                    app_instance.ls()

    output = stdout.getvalue()
    assert "ID" in output
    assert "Tax Reporter" in output
    assert "Details" in output


def test_rm_returns_without_change_on_back() -> None:
    """rm() should not delete entries when prompt is cancelled."""
    app_instance = app.App()
    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title="T",
            details="D",
            data={"path": "/tmp/not-used.csv"},
        )
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui.questionary, "checkbox", return_value=DummyQuestion()):
            with patch.object(app.ui, "_ask", return_value="__back__"):
                with patch.object(app.TaxReporterRegistry, "unregister") as unregister:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.rm()

    unregister.assert_not_called()
    reset.assert_not_called()


def test_rm_returns_without_change_on_empty_selection() -> None:
    """rm() should not delete entries when no checkbox item is selected."""
    app_instance = app.App()
    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title="T",
            details="D",
            data={"path": "/tmp/not-used.csv"},
        )
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui.questionary, "checkbox", return_value=DummyQuestion()):
            with patch.object(app.ui, "_ask", return_value=[]):
                with patch.object(app.TaxReporterRegistry, "unregister") as unregister:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.rm()

    unregister.assert_not_called()
    reset.assert_not_called()


def test_rm_unregisters_selected_entries_and_resets() -> None:
    """rm() should unregister selected entries and reset in-session report cache."""
    app_instance = app.App()

    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title="A",
            details="A",
            data={"path": "/tmp/a.csv"},
        ),
        _entry(
            entry_id="2",
            key=DummyFileReporter.__name__,
            title="B",
            details="B",
            data={"path": "/tmp/b.csv"},
        ),
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui.questionary, "checkbox", return_value=DummyQuestion()) as checkbox:
            with patch.object(app.ui, "_ask", return_value=["2"]):
                with patch.object(app.TaxReporterRegistry, "unregister") as unregister:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.rm()

    unregister.assert_called_once_with("2")
    reset.assert_called_once()

    choice_labels = [choice.title for choice in checkbox.call_args.kwargs["choices"]]
    assert "#1 Dummy File (File: a.csv)" in choice_labels
    assert "#2 Dummy File (File: b.csv)" in choice_labels


def test_rm_calls_reset_even_when_selection_matches_nothing() -> None:
    """Non-empty selection should trigger reset even if no entry id matches."""
    app_instance = app.App()

    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title="A",
            details="A",
            data={"path": "/tmp/a.csv"},
        ),
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui.questionary, "checkbox", return_value=DummyQuestion()):
            with patch.object(app.ui, "_ask", return_value=["999"]):
                with patch.object(app.TaxReporterRegistry, "unregister") as unregister:
                    with patch.object(app_instance, "_reset") as reset:
                        app_instance.rm()

    unregister.assert_called_once_with("999")
    reset.assert_called_once()


def test_report_success_sets_tax_report_state_and_calls_show(tmp_path: Path) -> None:
    """report() should prepare in-session tax-report state and call show()."""
    app_instance = app.App()
    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title=DummyFileReporter.name(),
            details="File: raw.csv",
            data={"path": str((tmp_path / "raw.csv").resolve())},
        )
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui, "with_prepare_animation", side_effect=lambda method: method):
            with patch.object(
                DummyFileReporter, "generate", return_value=_report(trade_revenue=1.0)
            ):
                with patch.object(app_instance, "show") as show:
                    app_instance.report()

    assert app_instance.tax_report is not None
    assert app_instance.tax_report[2025].trade_revenue == 1.0
    assert not app_instance.logs
    show.assert_called_once()


def test_report_aggregates_all_supported_reporters_and_messages(tmp_path: Path) -> None:
    """report() should instantiate each reporter key branch and aggregate generated data."""
    app_instance = app.App()

    any_path = str((tmp_path / "dummy.csv").resolve())
    entries = [
        _entry(
            entry_id="1",
            key=CharlesSchwabEmployeeSponsoredTaxReporter.__name__,
            title=CharlesSchwabEmployeeSponsoredTaxReporter.name(),
            details="File: schwab.json",
            data={"path": any_path},
        ),
        _entry(
            entry_id="2",
            key=IBKRTaxReporter.__name__,
            title=IBKRTaxReporter.name(),
            details="Query ID: 7",
            data={"query_id": "7", "token": "t"},
        ),
        _entry(
            entry_id="3",
            key=RevolutInterestTaxReporter.__name__,
            title=RevolutInterestTaxReporter.name(),
            details="File: revolut.csv",
            data={"path": any_path},
        ),
        _entry(
            entry_id="4",
            key=CoinbaseTaxReporter.__name__,
            title=CoinbaseTaxReporter.name(),
            details="File: coinbase.csv",
            data={"path": any_path},
        ),
        _entry(
            entry_id="5",
            key=EmploymentTaxReporter.__name__,
            title=EmploymentTaxReporter.name(),
            details="Year: 2025",
            data={
                "year": 2025,
                "employment_revenue": 1.0,
                "employment_cost": 0.0,
                "social_security_contributions": 0.0,
                "donations": 0.0,
            },
        ),
        _entry(
            entry_id="6",
            key=DummyFileReporter.__name__,
            title=DummyFileReporter.name(),
            details="File: raw.csv",
            data={"path": any_path},
        ),
    ]

    def _gen(report: TaxReport, log: str):
        def _inner(
            self: TaxReporter,
            logs: TaxReportLogs | None = None,
        ) -> TaxReport:
            if logs is not None:
                log_date, _, log_msg = log.partition(" ")
                action, _, detail = log_msg.partition(" ")
                if not detail:
                    detail = action
                self.update_logs(
                    datetime.strptime(log_date, "%m/%d/%Y").date(),
                    action,
                    detail,
                    changes=[{"name": "Status", "before": "before", "after": "updated"}],
                    logs=logs,
                )
            return report

        return _inner

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui, "with_prepare_animation", side_effect=lambda method: method):
            with patch.object(
                CharlesSchwabEmployeeSponsoredTaxReporter,
                "generate",
                _gen(_report(trade_revenue=1.0), "01/01/2025 S"),
            ):
                with patch.object(
                    IBKRTaxReporter,
                    "generate",
                    _gen(_report(trade_revenue=2.0), "01/02/2025 I"),
                ):
                    with patch.object(
                        RevolutInterestTaxReporter,
                        "generate",
                        _gen(_report(domestic_interest=3.0), "01/03/2025 R"),
                    ):
                        with patch.object(
                            CoinbaseTaxReporter,
                            "generate",
                            _gen(_report(crypto_revenue=4.0), "01/04/2025 C"),
                        ):
                            with patch.object(
                                EmploymentTaxReporter,
                                "generate",
                                _gen(_report(employment_revenue=5.0), "01/05/2025 E"),
                            ):
                                with patch.object(
                                    DummyFileReporter,
                                    "generate",
                                    _gen(_report(trade_cost=6.0), "01/06/2025 X"),
                                ):
                                    with patch.object(app_instance, "show") as show:
                                        app_instance.report()

    assert app_instance.tax_report is not None
    tax_record = app_instance.tax_report[2025]
    assert tax_record.trade_revenue == 3.0
    assert tax_record.trade_cost == 6.0
    assert tax_record.crypto_revenue == 4.0
    assert tax_record.domestic_interest == 3.0
    assert tax_record.employment_revenue == 5.0
    assert len(app_instance.logs) == 6
    assert any("01/01/2025" in log for log in app_instance.logs)
    assert any("01/06/2025" in log for log in app_instance.logs)
    show.assert_called_once()


def test_report_failure_for_invalid_reporter_object_prints_frame_and_waits_back() -> None:
    """Invalid deserialized reporter should show framed error output."""
    app_instance = app.App()
    question = DummyQuestion("__back__")
    entries = [("1", object())]
    with patch.object(
        app.TaxReporterRegistry,
        "deserialize_all",
        return_value=entries,
    ):
        with patch.object(app.ui, "with_prepare_animation", side_effect=lambda method: method):
            with patch.object(app.ui.questionary, "text", return_value=question) as text:
                with patch.object(app.ui, "_ask", return_value="__back__") as ask:
                    with patch.object(app_instance, "show") as show:
                        with patch.object(app.sys, "stdout", new=io.StringIO()) as stdout:
                            app_instance.report()

    output = stdout.getvalue()
    assert "Traceback (most recent call last):" in output
    assert "generate" in output
    assert "\x1b[31m┌" in output
    text.assert_called_once_with("[esc to back]", erase_when_done=True)
    ask.assert_called_once_with(question, block_typed_input=True)
    show.assert_not_called()


def test_report_failure_from_generate_exception_prints_frame_and_waits_back(tmp_path: Path) -> None:
    """Reporter generate() exceptions should be framed and handled without crash."""
    app_instance = app.App()
    question = DummyQuestion("__back__")
    entries = [
        _entry(
            entry_id="1",
            key=DummyFileReporter.__name__,
            title=DummyFileReporter.name(),
            details="File: raw.csv",
            data={"path": str((tmp_path / "raw.csv").resolve())},
        )
    ]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui, "with_prepare_animation", side_effect=lambda method: method):
            with patch.object(DummyFileReporter, "generate", side_effect=RuntimeError("boom")):
                with patch.object(app.ui.questionary, "text", return_value=question) as text:
                    with patch.object(app.ui, "_ask", return_value="__back__") as ask:
                        with patch.object(app_instance, "show") as show:
                            with patch.object(app.sys, "stdout", new=io.StringIO()) as stdout:
                                app_instance.report()

    output = stdout.getvalue()
    assert "Traceback (most recent call last):" in output
    assert "boom" in output
    text.assert_called_once_with("[esc to back]", erase_when_done=True)
    ask.assert_called_once_with(question, block_typed_input=True)
    show.assert_not_called()


def test_report_uses_prepare_animation_wrapper() -> None:
    """report() should execute report preparation via UI wrapper."""
    app_instance = app.App()
    entries = [("1", DummyFileReporter("/tmp/raw.csv"))]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(
            app.ui, "with_prepare_animation", side_effect=lambda method: method
        ) as wrapper:
            with patch.object(DummyFileReporter, "generate", return_value=TaxReport()):
                with patch.object(app_instance, "show"):
                    app_instance.report()

    wrapper.assert_called_once()


def test_report_overwrites_previous_messages_and_tax_report() -> None:
    """report() success path should replace previous in-memory report and messages."""
    app_instance = app.App()
    app_instance.tax_report = _report(trade_revenue=999.0)
    app_instance.logs = TaxReportLogs()
    app_instance.logs.add(datetime(2025, 1, 1).date(), "old log")
    entries = [("1", DummyFileReporter("/tmp/raw.csv"))]

    with patch.object(app.TaxReporterRegistry, "deserialize_all", return_value=entries):
        with patch.object(app.ui, "with_prepare_animation", side_effect=lambda method: method):
            with patch.object(
                DummyFileReporter, "generate", return_value=_report(trade_revenue=1.0)
            ):
                with patch.object(app_instance, "show"):
                    app_instance.report()

    assert app_instance.tax_report is not None
    assert app_instance.tax_report[2025].trade_revenue == 1.0
    assert app_instance.logs == ["old log"]


def test_with_prepare_animation_writes_spinner_and_clear_line() -> None:
    """UI decorator should emit progress line and clear final line."""

    class FakeEvent:
        """Threading event double controlling animation loop lifecycle."""

        def __init__(self) -> None:
            """Initialize event state."""
            self.flag = False
            self.calls = 0

        def is_set(self) -> bool:
            """Return whether animation should stop."""
            self.calls += 1
            return self.flag or self.calls > 1

        def set(self) -> None:
            """Signal animation loop stop."""
            self.flag = True

    class FakeThread:
        """Thread double that runs target synchronously."""

        def __init__(self, *, target: Any, daemon: bool) -> None:
            """Store thread target and daemon marker."""
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            """Execute target immediately."""
            self.target()

        def join(self) -> None:
            """No-op join for synchronous execution."""
            return

    @ui_module.with_prepare_animation
    def _task() -> None:
        return

    with patch.object(ui_module, "_disable_tty_input_echo", return_value=_fake_context()):
        with patch.object(ui_module.threading, "Event", return_value=FakeEvent()):
            with patch.object(
                ui_module.threading,
                "Thread",
                side_effect=lambda target, daemon: FakeThread(target=target, daemon=daemon),
            ):
                with patch.object(ui_module.time, "sleep", return_value=None):
                    with patch.object(ui_module.sys, "stdout") as stdout:
                        _task()

    writes = [str(call_.args[0]) for call_ in stdout.write.call_args_list]
    assert any("Preparing tax summary" in value for value in writes)
    assert any("\x1b[2K" in value for value in writes)


def test_show_returns_without_ui_call_when_tax_report_is_missing() -> None:
    """show() should call UI print/wait helpers with current state."""
    app_instance = app.App()
    with patch.object(app.ui, "print_tax_report") as print_tax_report:
        with patch.object(app.ui, "wait_for_back_navigation") as wait_for_back:
            app_instance.show()
    print_tax_report.assert_called_once_with(None, [])
    wait_for_back.assert_called_once()


def test_show_delegates_to_ui_with_tax_report_and_logs() -> None:
    """show() should delegate printing and back wait to UI helpers."""
    app_instance = app.App()
    app_instance.tax_report = _report(trade_revenue=1234.5)
    app_instance.logs = TaxReportLogs()
    app_instance.logs.add(datetime(2025, 1, 1).date(), "log one")
    app_instance.logs.add(datetime(2025, 1, 2).date(), "log two")
    with patch.object(app.ui, "print_tax_report") as print_tax_report:
        with patch.object(app.ui, "wait_for_back_navigation") as wait_for_back:
            app_instance.show()
    print_tax_report.assert_called_once_with(app_instance.tax_report, app_instance.logs)
    wait_for_back.assert_called_once()


def test_reset_clears_report_messages() -> None:
    """reset() should clear transient in-session report state."""
    app_instance = app.App()
    app_instance.tax_report = _report(trade_revenue=999.0)
    app_instance.logs = TaxReportLogs()
    app_instance.logs.add(datetime(2025, 1, 1).date(), "cached report")

    app_instance._reset()

    assert app_instance.tax_report is None
    assert not app_instance.logs


def test_main_runs_app_loop_once() -> None:
    """main() should create app instance and run interactive loop."""
    instance = app.App()
    with patch.object(app, "App", return_value=instance):
        with patch.object(instance, "run") as run:
            app.main()
    run.assert_called_once()


def test_main_handles_keyboard_interrupt_with_reset_and_exit_zero() -> None:
    """main() should delegate Ctrl-C handling to app.exit_app()."""
    instance = app.App()
    with patch.object(app, "App", return_value=instance):
        with patch.object(instance, "run", side_effect=KeyboardInterrupt):
            with patch.object(instance, "exit_app") as exit_app:
                app.main()
    exit_app.assert_called_once()


def test_main_does_not_call_sys_exit_on_normal_return() -> None:
    """main() should return normally when run loop exits without exception."""
    instance = app.App()
    with patch.object(app, "App", return_value=instance):
        with patch.object(instance, "run"):
            with patch.object(app.sys, "exit") as exit_:
                app.main()
    exit_.assert_not_called()


def test_exit_app_resets_state_and_exits_with_zero_status() -> None:
    """exit_app() should clear state and exit process with status code 0."""
    app_instance = app.App()
    app_instance.tax_report = _report(trade_revenue=1.0)
    app_instance.logs = TaxReportLogs()
    app_instance.logs.add(datetime(2025, 1, 1).date(), "log")

    with patch.object(app_instance, "_reset") as reset:
        with patch.object(app.sys, "exit") as exit_:
            app_instance.exit_app()

    reset.assert_called_once()
    exit_.assert_called_once_with(0)
