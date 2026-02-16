"""Tests for console app helper functions and control flow."""

import io
import tempfile
from base64 import b64decode, b64encode
from io import UnsupportedOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call, patch

import pytest
import yaml
from prompt_toolkit.key_binding import KeyBindings

from src import app, caches
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
    """Test case."""
    question = DummyQuestion("ok")
    assert question.peek_result() == "ok"
    result = _call_module("_ask", question)
    assert result == "ok"
    assert question.application.ttimeoutlen == 0
    assert question.application.timeoutlen == 0


def test_bind_escape_back_rebinds_key_bindings() -> None:
    """Test case."""
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


def test_bind_escape_back_blocks_typed_keys_when_flag_set() -> None:
    """Test case."""
    question = DummyQuestion("ok")
    setattr(question, "_block_typed_input", True)
    bound = _call_module("_bind_escape_back", question)
    assert any(
        getattr(binding.keys[0], "value", binding.keys[0]) == "c-m"
        for binding in bound.application.key_bindings.bindings
    )
    zero_binding = next(
        binding for binding in bound.application.key_bindings.bindings if binding.keys == ("0",)
    )
    alpha_binding = next(
        binding for binding in bound.application.key_bindings.bindings if binding.keys == ("k",)
    )
    assert any(binding.keys == ("9",) for binding in bound.application.key_bindings.bindings)
    zero_binding.handler(SimpleNamespace())
    alpha_binding.handler(SimpleNamespace())


def test_clear_last_lines_writes_escape_sequences() -> None:
    """Test case."""
    with patch.object(app.sys, "stdout") as stdout:
        _call_module("_clear_last_lines", 2)
    assert stdout.write.call_args_list == [
        call("\x1b[1A\x1b[2K"),
        call("\x1b[1A\x1b[2K"),
        call("\r"),
    ]
    stdout.flush.assert_called_once()


def test_clear_last_lines_noop_for_zero() -> None:
    """Test case."""
    with patch.object(app.sys, "stdout") as stdout:
        _call_module("_clear_last_lines", 0)
    stdout.write.assert_not_called()
    stdout.flush.assert_not_called()


def test_disable_tty_input_echo_non_tty() -> None:
    """Test case."""
    with patch.object(app.sys.stdin, "fileno", return_value=0):
        with patch.object(app.os, "isatty", return_value=False):
            with patch.object(app.termios, "tcgetattr") as tcgetattr:
                with _call_module("_disable_tty_input_echo"):
                    pass
    tcgetattr.assert_not_called()


def test_disable_tty_input_echo_unsupported_fileno() -> None:
    """Test case."""
    with patch.object(app.sys.stdin, "fileno", side_effect=UnsupportedOperation):
        with patch.object(app.termios, "tcgetattr") as tcgetattr:
            with _call_module("_disable_tty_input_echo"):
                pass
    tcgetattr.assert_not_called()


def test_disable_tty_input_echo_tty_mode() -> None:
    """Test case."""
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
    """Test case."""

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


@patch("src.app.questionary.select")
def test_prompt_main_action_with_no_registries_disables_registry_actions(select: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(app, "_load_registered_entries", return_value=[]):
        with patch.object(app, "_ask", return_value="register"):
            action = _call_method(app_instance, "_prompt_main_action")
    assert action == "register"
    choices = select.call_args.kwargs["choices"]
    assert choices[1].disabled == "No registered tax reporters"
    assert choices[2].disabled == "No registered tax reporters"
    assert choices[3].disabled == "No registered tax reporters"
    assert choices[4].disabled == "No prepared report in this session"


@patch("src.app.questionary.select")
def test_prompt_main_action_with_registries_enables_registry_actions(select: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(
        app,
        "_load_registered_entries",
        return_value=[cast(app.RegisteredTaxReportEntry, {})],
    ):
        with patch.object(app, "_ask", return_value="report"):
            action = _call_method(app_instance, "_prompt_main_action")
    assert action == "report"
    choices = select.call_args.kwargs["choices"]
    assert [choice.title for choice in choices] == (
        "Register tax reporter|List tax reporters|Remove tax reporters|Prepare tax report|"
        "Show tax report|Exit"
    ).split("|")
    assert choices[1].disabled is None
    assert choices[2].disabled is None
    assert choices[3].disabled is None
    assert choices[4].disabled == "No prepared report in this session"


@patch("src.app.questionary.select")
def test_prompt_main_action_enables_show_when_session_report_exists(select: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    app_instance.tax_report = TaxReport()
    select.return_value = object()
    with patch.object(app, "_load_registered_entries", return_value=[]):
        with patch.object(app, "_ask", return_value="show"):
            action = _call_method(app_instance, "_prompt_main_action")
    assert action == "show"
    choices = select.call_args.kwargs["choices"]
    assert choices[4].disabled is None


@patch("src.app.questionary.select")
def test_select_report_spec_back(select: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            result = _call_method(app_instance, "_select_report_spec")
    assert result == "__back__"


@patch("src.app.questionary.select")
def test_select_report_spec_returns_selected_spec(select: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    select.return_value = object()
    selected = ("ib_trade_cash", "Interactive Brokers Trade Cash", "api", DummyApiReporter)
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value=selected):
            result = _call_method(app_instance, "_select_report_spec")
    assert result == selected


@patch("src.app.questionary.text")
def test_collect_api_entry_back_on_query(text: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", return_value="__back__"):
            result = _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    assert result is None


@patch("src.app.questionary.text")
def test_collect_api_entry_normalizes_query_and_token(text: Mock) -> None:
    """Test case."""
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
    """Test case."""
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
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.side_effect = [object(), object()]
    with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
        with patch.object(app, "_ask", side_effect=["123", "__back__"]):
            result = _call_method(app_instance, "_collect_api_entry", "k", "t", DummyApiReporter)
    assert result is None


@patch("src.app.questionary.text")
def test_collect_api_entry_token_validation(text: Mock) -> None:
    """Test case."""
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
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_load_registered_entries", return_value=[]):
        with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
            with patch.object(app, "_ask", return_value="__back__"):
                result = _call_method(
                    app_instance, "_collect_file_entry", "k", "t", DummyFileReporter
                )
    assert result is None


@patch("src.app.questionary.text")
def test_collect_file_entry_returns_resolved_path(text: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "file.csv"
        path.write_text("x", encoding="utf-8")
        with patch.object(app, "_load_registered_entries", return_value=[]):
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value=str(path)):
                    entry = _call_method(
                        app_instance, "_collect_file_entry", "k", "t", DummyFileReporter
                    )
    assert entry is not None
    assert entry["tax_report_data"] == path.resolve()


@patch("src.app.questionary.text")
def test_collect_file_entry_validation_rules(text: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with patch.object(app, "_load_registered_entries", return_value=[]):
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
        assert validate(str(csv_path)) is True
    file_filter = text.call_args.kwargs["completer"].file_filter
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        csv_path = tmp_dir / "y.csv"
        csv_path.write_text("x", encoding="utf-8")
        assert file_filter(str(tmp_dir)) is True
        assert file_filter(str(csv_path)) is True
        txt_path = tmp_dir / "y.txt"
        txt_path.write_text("x", encoding="utf-8")
        assert file_filter(str(txt_path)) is False


@patch("src.app.questionary.text")
def test_collect_file_entry_rejects_already_registered_path(text: Mock) -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    text.return_value = object()
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "registered.csv"
        csv_path.write_text("x", encoding="utf-8")
        existing_entry: app.RegisteredTaxReportEntry = {
            "entry_id": 1,
            "tax_report_key": "k",
            "report_title": "t",
            "report_kind": "files",
            "report_cls": DummyFileReporter,
            "tax_report_data": csv_path.resolve(),
            "created_at": 1,
            "registry_path": Path(tmp) / "1.yaml",
        }
        with patch.object(app, "_load_registered_entries", return_value=[existing_entry]):
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value="__back__"):
                    _call_method(app_instance, "_collect_file_entry", "k", "t", DummyFileReporter)
        validate = text.call_args.kwargs["validate"]
        assert validate(str(csv_path)) == "File already registered for this report type."
        file_filter = text.call_args.kwargs["completer"].file_filter
        assert file_filter(str(csv_path)) is False


def test_collect_submission_entry_retries_after_back_from_inner() -> None:
    """Test case."""
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
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    with patch.object(app_instance, "_select_report_spec", return_value="__back__"):
        result = _call_method(app_instance, "_collect_submission_entry")
    assert result is None


def test_collect_submission_entry_uses_api_collector() -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    spec = ("ib_trade_cash", "Interactive Brokers Trade Cash", "api", DummyApiReporter)
    entry = {
        "tax_report_key": "ib_trade_cash",
        "report_title": "Interactive Brokers Trade Cash",
        "report_kind": "api",
        "report_cls": DummyApiReporter,
        "tax_report_data": {"query_id": "1", "token": "x"},
    }
    with patch.object(app_instance, "_select_report_spec", return_value=spec):
        with patch.object(app_instance, "_collect_api_entry", return_value=entry) as api_collect:
            result = _call_method(app_instance, "_collect_submission_entry")
    api_collect.assert_called_once()
    assert result == entry


def _encode(content: str) -> str:
    return b64encode(content.encode("utf-8")).decode("ascii")


def _decode(content: str) -> str:
    return b64decode(content.encode("ascii"), validate=True).decode("utf-8")


def _registry_home() -> Path:
    return getattr(caches, "_registry_dir")()


def _ensure_registry_directory() -> Path:
    path = _registry_home()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def test_registry_payload_base64_roundtrip_and_invalid_decode() -> None:
    """Test case."""
    content = "query_id: 7\ntoken: abc\n"
    encoded = _encode(content)
    assert encoded != content
    assert _decode(encoded) == content
    with pytest.raises(Exception):
        _decode("%%%")


def test_register_writes_with_mocked_id_source_even_with_invalid_files_present() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.dict(app.os.environ, {caches.REGISTRY_DIR_ENV_VAR: tmp_dir}, clear=False):
            registry = _ensure_registry_directory()
            (registry / "subdir").mkdir()
            (registry / "invalid-name.txt").write_text("x", encoding="utf-8")
            (registry / "0000--unknown.path").write_text("x", encoding="utf-8")
            (registry / "0001--ib_trade_cash.path").write_text("x", encoding="utf-8")
            (registry / "0002--raw_custom_csv.path").write_text("", encoding="utf-8")
            (registry / "0003--raw_custom_csv.path").write_text(
                "%%%not-base64%%%", encoding="utf-8"
            )
            (registry / "0004--raw_custom_csv.path").write_text(_encode("   "), encoding="utf-8")
            (registry / "0005--ib_trade_cash.yaml").write_text(
                _encode("# comment\nbroken_line\nquery_id: 9\ntoken: t\n"),
                encoding="utf-8",
            )
            (registry / "0006--ib_trade_cash.yaml").write_text(
                _encode("query_id: 1"), encoding="utf-8"
            )
            source = Path(tmp_dir) / "register.csv"
            source.write_text("x", encoding="utf-8")
            entry_file: app.TaxReportEntry = {
                "tax_report_key": "ib_trade_cash",
                "report_title": "Interactive Brokers Trade Cash",
                "report_kind": "api",
                "report_cls": app.IBTradeCashTaxReporter,
                "tax_report_data": {"query_id": "12", "token": "tok"},
            }
            with patch.object(app, "read_registry_entry_ids", return_value=[]):
                with patch.object(app, "uuid4", return_value=SimpleNamespace(int=333_333_333_333)):
                    with patch.object(app.time, "time_ns", return_value=7):
                        with patch.object(
                            app.PolishPitConsoleApp,
                            "_collect_submission_entry",
                            return_value=entry_file,
                        ):
                            app.PolishPitConsoleApp().register()
            created_file = registry / "433333333.yaml"
            assert created_file.is_file()


def test_register_returns_without_writing_on_cancel() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.dict(app.os.environ, {caches.REGISTRY_DIR_ENV_VAR: tmp_dir}, clear=False):
            registry = _ensure_registry_directory()
            with patch.object(
                app.PolishPitConsoleApp,
                "_collect_submission_entry",
                return_value=None,
            ):
                app.PolishPitConsoleApp().register()
            assert sorted(path.name for path in registry.iterdir()) == []


def test_register_writes_without_loading_existing_entries() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.dict(app.os.environ, {caches.REGISTRY_DIR_ENV_VAR: tmp_dir}, clear=False):
            registry = _ensure_registry_directory()
            source = Path(tmp_dir) / "x.csv"
            source.write_text("x", encoding="utf-8")
            (registry / "0001--raw_custom_csv.yaml").write_text(
                _encode(f"path: {source.resolve()}\n"),
                encoding="utf-8",
            )
            (registry / "0001--ib_trade_cash.yaml").write_text(
                _encode("query_id: 1\ntoken: t\n"),
                encoding="utf-8",
            )
            entry_file: app.TaxReportEntry = {
                "tax_report_key": "ib_trade_cash",
                "report_title": "Interactive Brokers Trade Cash",
                "report_kind": "api",
                "report_cls": app.IBTradeCashTaxReporter,
                "tax_report_data": {"query_id": "12", "token": "tok"},
            }
            with patch.object(app, "uuid4", return_value=SimpleNamespace(int=111_111_111_111)):
                with patch.object(app.time, "time_ns", return_value=9):
                    with patch.object(
                        app.PolishPitConsoleApp,
                        "_collect_submission_entry",
                        return_value=entry_file,
                    ):
                        app.PolishPitConsoleApp().register()
            created_file = registry / "511111111.yaml"
            assert created_file.is_file()
            decoded = _decode(created_file.read_text(encoding="utf-8"))
            assert yaml.safe_load(decoded) == {
                "tax_report_key": "ib_trade_cash",
                "query_id": "12",
                "token": "tok",
                "created_at": 9,
            }


def test_register_writes_file_and_api_entries() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.dict(app.os.environ, {caches.REGISTRY_DIR_ENV_VAR: tmp_dir}, clear=False):
            source = Path(tmp_dir) / "ib.csv"
            source.write_text("x", encoding="utf-8")
            entry_file: app.TaxReportEntry = {
                "tax_report_key": "raw_custom_csv",
                "report_title": "Raw Custom CSV",
                "report_kind": "files",
                "report_cls": app.RawTaxReporter,
                "tax_report_data": source,
            }
            with patch.object(app, "read_registry_entry_ids", return_value=[]):
                with patch.object(app, "uuid4", return_value=SimpleNamespace(int=123_456_789_012)):
                    with patch.object(app.time, "time_ns", return_value=10):
                        with patch.object(
                            app.PolishPitConsoleApp,
                            "_collect_submission_entry",
                            return_value=entry_file,
                        ):
                            app.PolishPitConsoleApp().register()

            first_file = _registry_home() / "256789012.yaml"
            assert first_file.is_file()
            decoded = _decode(first_file.read_text(encoding="utf-8"))
            assert decoded is not None
            assert yaml.safe_load(decoded) == {
                "tax_report_key": "raw_custom_csv",
                "path": str(source.resolve()),
                "created_at": 10,
            }

            entry_api: app.TaxReportEntry = {
                "tax_report_key": "ib_trade_cash",
                "report_title": "Interactive Brokers Trade Cash",
                "report_kind": "api",
                "report_cls": app.IBTradeCashTaxReporter,
                "tax_report_data": {"query_id": "12", "token": "tok"},
            }
            with patch.object(app, "read_registry_entry_ids", return_value=[256_789_012]):
                with patch.object(app, "uuid4", return_value=SimpleNamespace(int=987_654_321_098)):
                    with patch.object(app.time, "time_ns", return_value=20):
                        with patch.object(
                            app.PolishPitConsoleApp,
                            "_collect_submission_entry",
                            return_value=entry_api,
                        ):
                            app.PolishPitConsoleApp().register()

            second_file = _registry_home() / "454321098.yaml"
            assert second_file.is_file()
            decoded = _decode(second_file.read_text(encoding="utf-8"))
            assert decoded is not None
            assert yaml.safe_load(decoded) == {
                "tax_report_key": "ib_trade_cash",
                "query_id": "12",
                "token": "tok",
                "created_at": 20,
            }


def test_ls_empty_and_populated_outputs() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry_dir = Path(tmp_dir) / "registry"
        with patch.dict(
            app.os.environ,
            {caches.REGISTRY_DIR_ENV_VAR: str(registry_dir)},
            clear=False,
        ):
            with patch("src.app.questionary.text", return_value=SimpleNamespace()) as text_prompt:
                with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                    with patch.object(app, "_ask", return_value="__back__") as ask:
                        with patch.object(app, "_clear_last_lines") as clear_last_lines:
                            with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                                app.PolishPitConsoleApp().ls()
            output = out.getvalue()
            assert "ID" in output
            assert "Tax report" in output
            assert text_prompt.call_count == 1
            ask.assert_called_once()
            clear_last_lines.assert_called_once()

            registry = _ensure_registry_directory()
            source = Path(tmp_dir) / "raw.csv"
            source.write_text("year,description,trade_revenue\n2025,x,1\n", encoding="utf-8")
            (registry / "1.yaml").write_text(
                _encode(
                    f"tax_report_key: raw_custom_csv\npath: {source.resolve()}\ncreated_at: 20\n"
                ),
                encoding="utf-8",
            )
            (registry / "2.yaml").write_text(
                _encode("tax_report_key: ib_trade_cash\nquery_id: 5\ntoken: t\ncreated_at: 10\n"),
                encoding="utf-8",
            )
            with patch("src.app.questionary.text", return_value=object()) as text_prompt:
                with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                    with patch.object(app, "_ask", return_value="__back__"):
                        with patch.object(app, "_clear_last_lines") as clear_last_lines:
                            with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                                app.PolishPitConsoleApp().ls()
            output = out.getvalue()
            assert "Raw Custom CSV" in output
            assert "Interactive Brokers Trade Cash" in output
            assert "000000001" in output
            assert "000000002" in output
            assert output.find("Interactive Brokers Trade Cash") < output.find("Raw Custom CSV")
            assert text_prompt.call_count == 1
            clear_last_lines.assert_called_once()
            assert clear_last_lines.call_args.args[0] > 0


@patch("src.app.questionary.checkbox")
def test_rm_empty_cancel_no_selection_and_success_paths(checkbox: Mock) -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry_dir = Path(tmp_dir) / "registry"
        with patch.dict(
            app.os.environ,
            {caches.REGISTRY_DIR_ENV_VAR: str(registry_dir)},
            clear=False,
        ):
            with patch("src.app.questionary.select", return_value=object()) as select:
                with patch.object(app, "_ask", return_value="__back__") as ask:
                    app.PolishPitConsoleApp().rm()
            select.assert_not_called()
            ask.assert_called_once()

            registry = _ensure_registry_directory()
            source = Path(tmp_dir) / "raw.csv"
            source.write_text("year,description,trade_revenue\n2025,x,1\n", encoding="utf-8")
            (registry / "1.yaml").write_text(
                _encode(f"tax_report_key: raw_custom_csv\npath: {source.resolve()}\n"),
                encoding="utf-8",
            )
            (registry / "2.yaml").write_text(
                _encode("tax_report_key: ib_trade_cash\nquery_id: 5\ntoken: t\n"),
                encoding="utf-8",
            )

            checkbox.return_value = object()

            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value="__back__"):
                    app.PolishPitConsoleApp().rm()
            assert (registry / "1.yaml").exists()
            assert (registry / "2.yaml").exists()

            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value=[]):
                    with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                        app.PolishPitConsoleApp().rm()
            assert out.getvalue() == ""
            assert (registry / "1.yaml").exists()
            assert (registry / "2.yaml").exists()

            app_instance = app.PolishPitConsoleApp()
            app_instance.tax_report = TaxReport({2025: TaxRecord(trade_revenue=1.0)})
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value=[999]):
                    with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                        app_instance.rm()
            assert out.getvalue() == ""
            assert app_instance.tax_report is not None
            assert (registry / "1.yaml").exists()
            assert (registry / "2.yaml").exists()

            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value=[2]):
                    with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                        app.PolishPitConsoleApp().rm()
            assert out.getvalue() == ""
            assert (registry / "1.yaml").exists()
            assert not (registry / "2.yaml").exists()

            choice_labels = [choice.title for choice in checkbox.call_args.kwargs["choices"]]
            assert any(label.startswith("#000000001 Raw Custom CSV") for label in choice_labels)
            assert any(
                label.startswith("#000000002 Interactive Brokers Trade Cash")
                for label in choice_labels
            )


def test_report_empty_and_populated_behavior() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry_dir = Path(tmp_dir) / "registry"
        with patch.dict(
            app.os.environ,
            {caches.REGISTRY_DIR_ENV_VAR: str(registry_dir)},
            clear=False,
        ):
            with patch.object(app.sys, "stdout", new=io.StringIO()) as out:
                app.PolishPitConsoleApp().report()
            assert "No registered tax reporters." in out.getvalue()

            registry = _ensure_registry_directory()
            source = Path(tmp_dir) / "raw.csv"
            source.write_text("year,description,trade_revenue\n2025,x,10\n", encoding="utf-8")
            (registry / "1.yaml").write_text(
                _encode(f"tax_report_key: raw_custom_csv\npath: {source.resolve()}\n"),
                encoding="utf-8",
            )
            (registry / "2.yaml").write_text(
                _encode("tax_report_key: ib_trade_cash\nquery_id: 1\ntoken: t\n"),
                encoding="utf-8",
            )
            api_report = TaxReport({2025: TaxRecord(trade_revenue=5.0)})
            with patch.object(app.IBTradeCashTaxReporter, "generate", return_value=api_report):
                with patch.object(app, "_run_prepare_animation", side_effect=lambda stop: None):
                    app_instance = app.PolishPitConsoleApp()
                    with patch.object(app.sys.stdin, "fileno", return_value=0):
                        with patch.object(app.os, "isatty", return_value=False):
                            with patch("src.app.questionary.text", return_value=object()):
                                with patch.object(
                                    app, "_bind_escape_back", side_effect=lambda q: q
                                ):
                                    with patch.object(app, "_ask", return_value="__back__"):
                                        with patch.object(
                                            app.sys, "stdout", new=io.StringIO()
                                        ) as out:
                                            app_instance.report()
            assert "2025" in out.getvalue()
            assert app_instance.tax_report is not None
            assert app_instance.pending_clear_lines > 0
            with patch.object(app_instance, "_build_tax_report", return_value=None):
                with patch.object(app, "_run_prepare_animation", side_effect=lambda stop: None):
                    with patch.object(app.sys.stdin, "fileno", return_value=0):
                        with patch.object(app.os, "isatty", return_value=False):
                            with patch.object(app.sys, "stdout", new=io.StringIO()):
                                app_instance.report()
            for prompt in (SimpleNamespace(), object()):
                with patch.object(
                    app.IBTradeCashTaxReporter, "generate", side_effect=RuntimeError("boom")
                ):
                    with patch.object(app, "_run_prepare_animation", side_effect=lambda stop: None):
                        with patch.object(app.sys.stdin, "fileno", return_value=0):
                            with patch.object(app.os, "isatty", return_value=False):
                                with patch("src.app.questionary.text", return_value=prompt):
                                    with patch.object(
                                        app, "_bind_escape_back", side_effect=lambda q: q
                                    ):
                                        with patch.object(app, "_ask", return_value="__back__"):
                                            with patch.object(
                                                app.sys, "stdout", new=io.StringIO()
                                            ) as out:
                                                app_instance.report()
                assert "Prepare report failed:" in out.getvalue()


def test_run_clears_pending_lines_before_prompt() -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    app_instance.pending_clear_lines = 3
    with patch.object(app_instance, "_prompt_main_action", return_value="exit"):
        with patch.object(app, "_clear_last_lines") as clear_last_lines:
            with pytest.raises(SystemExit):
                app_instance.run()
    clear_last_lines.assert_called_once_with(3)
    assert app_instance.pending_clear_lines == 0


def test_show_report_handles_missing_and_cached_report() -> None:
    """Test case."""
    app_instance = app.PolishPitConsoleApp()
    with patch("src.app.questionary.text") as text:
        app_instance.show_report()
    text.assert_not_called()

    app_instance.tax_report = TaxReport({2025: TaxRecord(trade_revenue=1.0)})
    with patch.object(app_instance, "_print_tax_summary", return_value=2):
        with patch("src.app.questionary.text", return_value=object()):
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q) as bind:
                with patch.object(app, "_ask", return_value="__back__") as ask:
                    app_instance.show_report()
    assert app_instance.pending_clear_lines == 2
    bind.assert_called_once()
    ask.assert_called_once()

    with patch.object(app_instance, "_print_tax_summary", return_value=3):
        with patch("src.app.questionary.text", return_value=SimpleNamespace()):
            with patch.object(app, "_bind_escape_back", side_effect=lambda q: q):
                with patch.object(app, "_ask", return_value="__back__"):
                    app_instance.show_report()
    assert app_instance.pending_clear_lines == 3


def test_main_dispatches_selected_commands_and_exits() -> None:
    """Test case."""
    with patch.object(
        app,
        "_load_registered_entries",
        return_value=[cast(app.RegisteredTaxReportEntry, {})],
    ):
        with patch.object(
            app,
            "_ask",
            side_effect=["register", "ls", "rm", "report", "show", "exit"],
        ):
            with patch.object(app.PolishPitConsoleApp, "register") as register:
                with patch.object(app.PolishPitConsoleApp, "ls") as ls:
                    with patch.object(app.PolishPitConsoleApp, "rm") as rm:
                        with patch.object(app.PolishPitConsoleApp, "report") as report:
                            with patch.object(
                                app.PolishPitConsoleApp, "show_report"
                            ) as show_report:
                                with pytest.raises(SystemExit):
                                    app.main()
    register.assert_called_once()
    ls.assert_called_once()
    rm.assert_called_once()
    report.assert_called_once()
    show_report.assert_called_once()


def test_main_disables_registry_actions_when_registry_is_empty() -> None:
    """Test case."""
    with patch.object(app, "_load_registered_entries", return_value=[]):
        with patch("src.app.questionary.select", return_value=object()) as select:
            with patch.object(app, "_ask", return_value="exit"):
                with pytest.raises(SystemExit):
                    app.main()
    choices = select.call_args.kwargs["choices"]
    titles = [choice.title for choice in choices]
    assert titles == [
        "Register tax reporter",
        "List tax reporters",
        "Remove tax reporters",
        "Prepare tax report",
        "Show tax report",
        "Exit",
    ]
    disabled_map = {choice.title: choice.disabled for choice in choices}
    assert disabled_map["Register tax reporter"] is None
    assert disabled_map["List tax reporters"] == "No registered tax reporters"
    assert disabled_map["Remove tax reporters"] == "No registered tax reporters"
    assert disabled_map["Prepare tax report"] == "No registered tax reporters"
    assert disabled_map["Show tax report"] == "No prepared report in this session"
    assert disabled_map["Exit"] is None


def test_main_disables_registry_actions_when_registry_has_only_invalid_entries() -> None:
    """Test case."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.dict(app.os.environ, {caches.REGISTRY_DIR_ENV_VAR: tmp_dir}, clear=False):
            registry = Path(tmp_dir)
            registry.mkdir(parents=True, exist_ok=True)
            (registry / "1.yaml").write_text(_encode("path: /tmp/source.csv\n"), encoding="utf-8")
            with pytest.raises(KeyError, match="tax_report_key"):
                app.main()


def test_main_exits_zero_on_keyboard_interrupt() -> None:
    """Test case."""
    with patch.object(app, "_ask", side_effect=KeyboardInterrupt):
        with patch.object(app.sys, "exit", side_effect=SystemExit) as exit_mock:
            with pytest.raises(SystemExit):
                app.main()
    exit_mock.assert_called_once_with(0)
