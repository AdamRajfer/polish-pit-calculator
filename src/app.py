"""Interactive console application for collecting and summarizing tax reports."""

import contextlib
import os
import sys
import termios
import threading
import time
import traceback
from datetime import datetime
from io import UnsupportedOperation
from numbers import Real
from pathlib import Path
from typing import Callable, Generator, Literal, TypedDict, cast
from uuid import uuid4

import questionary
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.shortcuts import clear as prompt_toolkit_clear
from questionary.prompts.path import GreatUXPathCompleter
from questionary.question import Question
from tabulate import tabulate

from src.caches import (
    read_registry_entry,
    read_registry_entry_ids,
    registry_entry_path,
    write_registry_entry,
)
from src.coinbase import CoinbaseTaxReporter
from src.config import TaxReport, TaxReporter
from src.ib import IBTradeCashTaxReporter
from src.raw import RawTaxReporter
from src.revolut import RevolutInterestTaxReporter
from src.schwab import SchwabEmployeeSponsoredTaxReporter

type ReportKind = Literal["files", "api"]
type MenuAction = Literal["register", "ls", "rm", "report", "show", "exit"]
type BackAction = Literal["__back__"]
type ReporterFactory = Callable[..., TaxReporter]
type ReportSpec = tuple[str, str, ReportKind, ReporterFactory]


def _entry_id_label(entry_id: int) -> str:
    """Format registry entry id for UI output as fixed 9 digits."""
    return f"{entry_id % 1_000_000_000:09d}"


class ApiTaxReportData(TypedDict):
    """Payload required for API-based report generation."""

    query_id: str
    token: str


class TaxReportEntry(TypedDict):
    """One submitted report item stored in application state."""

    tax_report_key: str
    report_title: str
    report_kind: ReportKind
    report_cls: ReporterFactory
    tax_report_data: Path | ApiTaxReportData


class GroupedFileEntry(TypedDict):
    """Grouped file reporter configuration used during summary build."""

    report_cls: ReporterFactory
    report_title: str
    paths: list[Path]


class RegisteredTaxReportEntry(TypedDict):
    """One registry entry loaded from on-disk command state."""

    entry_id: int
    tax_report_key: str
    report_title: str
    report_kind: ReportKind
    report_cls: ReporterFactory
    tax_report_data: Path | ApiTaxReportData
    created_at: int
    registry_path: Path


def _report_specs() -> tuple[ReportSpec, ...]:
    """Return supported reporter specs for interactive and registry workflows."""
    return (
        (
            "schwab_employee_sponsored",
            "Charles Schwab Employee Sponsored",
            "files",
            SchwabEmployeeSponsoredTaxReporter,
        ),
        (
            "ib_trade_cash",
            "Interactive Brokers Trade Cash",
            "api",
            IBTradeCashTaxReporter,
        ),
        (
            "coinbase_crypto",
            "Coinbase Crypto",
            "files",
            CoinbaseTaxReporter,
        ),
        (
            "revolut_interest",
            "Revolut Interest",
            "files",
            RevolutInterestTaxReporter,
        ),
        (
            "raw_custom_csv",
            "Raw Custom CSV",
            "files",
            RawTaxReporter,
        ),
    )


def _format_submission_details(entry: TaxReportEntry) -> str:
    """Build one-line details for a report entry."""
    if entry["report_kind"] == "files":
        path = cast(Path, entry["tax_report_data"])
        return f"File: {path.name}"
    query_id = cast(ApiTaxReportData, entry["tax_report_data"])["query_id"]
    return f"Query ID: {query_id}"


def _parse_registry_tax_report_data(
    payload_kind: str,
    payload: object,
) -> Path | ApiTaxReportData:
    """Parse deserialized registry payload."""
    payload_mapping = cast(dict[str, object], payload)
    if payload_kind == "path":
        return Path(cast(str, payload_mapping["path"]).strip()).expanduser().resolve()
    raw_query_id = cast(str | int, payload_mapping["query_id"])
    return {
        "query_id": str(int(str(raw_query_id).strip())),
        "token": cast(str, payload_mapping["token"]).strip(),
    }


def _parse_registered_entry(
    entry_id: int,
    report_specs_by_key: dict[str, ReportSpec],
) -> RegisteredTaxReportEntry:
    """Parse one registry file into a typed entry."""
    payload = read_registry_entry(entry_id)
    report_key_raw = cast(str, payload["tax_report_key"]).strip()
    report_spec = report_specs_by_key[report_key_raw]

    payload_kind = "path" if report_spec[2] == "files" else "yaml"
    tax_report_data = _parse_registry_tax_report_data(payload_kind, payload)

    registry_path = registry_entry_path(entry_id)
    created_at_raw = (
        payload["created_at"] if "created_at" in payload else registry_path.stat().st_mtime_ns
    )
    created_at = int(cast(int | str, created_at_raw))

    report_spec_key, report_title, report_kind, report_cls = report_spec
    return {
        "entry_id": entry_id,
        "tax_report_key": report_spec_key,
        "report_title": report_title,
        "report_kind": report_kind,
        "report_cls": report_cls,
        "tax_report_data": tax_report_data,
        "created_at": created_at,
        "registry_path": registry_path,
    }


def _load_registered_entries() -> list[RegisteredTaxReportEntry]:
    """Load all valid registered entries from on-disk registry files."""
    entries: list[RegisteredTaxReportEntry] = []
    report_specs_by_key = {spec[0]: spec for spec in _report_specs()}
    for entry_id in read_registry_entry_ids():
        entry = _parse_registered_entry(entry_id, report_specs_by_key)
        entries.append(entry)
    entries.sort(key=lambda entry: (entry["created_at"], entry["entry_id"]))
    return entries


def _ask(question: Question) -> object:
    """Run a Questionary prompt with zero ESC timeout latency."""
    question.application.ttimeoutlen = 0
    question.application.timeoutlen = 0
    return question.unsafe_ask()


def _bind_escape_back(question: Question) -> Question:
    """Bind ESC to return the explicit '__back__' sentinel value."""
    escape_bindings = KeyBindings()

    @escape_bindings.add("escape", eager=True)
    def _(_event: KeyPressEvent) -> None:
        """Exit prompt immediately and return a back sentinel."""
        _event.app.exit(result="__back__")

    question.application.key_bindings = merge_key_bindings(
        [escape_bindings, question.application.key_bindings]
    )
    if getattr(question, "_block_typed_input", False):
        readonly_bindings = KeyBindings()

        def _ignore_keypress(_event: KeyPressEvent) -> None:
            """Ignore blocked key presses for read-only back prompts."""
            return

        readonly_bindings.add("enter", eager=True)(_ignore_keypress)
        for codepoint in range(32, 127):
            readonly_bindings.add(chr(codepoint), eager=True)(_ignore_keypress)
        question.application.key_bindings = merge_key_bindings(
            [readonly_bindings, question.application.key_bindings]
        )
    return question


def _clear_last_lines(lines: int) -> None:
    """Clear the last N terminal lines produced by the app."""
    if lines <= 0:
        return
    for _ in range(lines):
        sys.stdout.write("\x1b[1A\x1b[2K")
    sys.stdout.write("\r")
    sys.stdout.flush()


def _clear_terminal_viewport() -> None:
    """Clear terminal viewport and scrollback, then move cursor to top-left."""
    prompt_toolkit_clear()
    sys.stdout.write("\x1b[3J\x1b[2J\x1b[H")
    sys.stdout.flush()


@contextlib.contextmanager
def _disable_tty_input_echo() -> Generator[None, None, None]:
    """Disable terminal input echo to avoid loader line corruption."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, UnsupportedOperation):
        yield
        return

    if not os.isatty(fd):
        yield
        return

    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(fd, termios.TCSANOW, new)
    termios.tcflush(fd, termios.TCIFLUSH)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        termios.tcflush(fd, termios.TCIFLUSH)


def _run_prepare_animation(stop_event: threading.Event) -> None:
    """Render a bouncing-star loader with cycling dot suffix."""
    spinner = "|/-\\"
    bar_width = 18
    index = 0
    while not stop_event.is_set():
        bounce = index % (2 * bar_width - 2)
        position = bounce if bounce < bar_width else (2 * bar_width - 2 - bounce)
        track = ["-"] * bar_width
        track[position] = "*"
        dots = "." * (index % 3 + 1)
        dot_suffix = dots.ljust(3)
        message = (
            f"\rPreparing tax summary{dot_suffix} "
            f"{spinner[index % len(spinner)]} [{''.join(track)}]"
        )
        sys.stdout.write(message)
        sys.stdout.flush()
        index += 1
        time.sleep(0.12)
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()


class PolishPitConsoleApp:
    """Stateful interactive console app for building tax summaries."""

    def __init__(self) -> None:
        """Initialize in-session prepared report cache."""
        self.tax_report: TaxReport | None = None
        self.report_messages: list[tuple[str, str]] = []
        self.pending_full_clear: bool = False
        self.pending_clear_lines: int = 0

    def run(self) -> None:
        """Run interactive registry command loop."""
        while True:
            if self.pending_full_clear:
                _clear_terminal_viewport()
                self.pending_full_clear = False
            if self.pending_clear_lines:
                _clear_last_lines(self.pending_clear_lines)
                self.pending_clear_lines = 0
            action = self._prompt_main_action()
            if action == "register":
                self.register()
            elif action == "ls":
                self.ls()
            elif action == "rm":
                self.rm()
            elif action == "report":
                self.report()
            elif action == "show":
                self.show_report()
            else:
                sys.exit(0)

    def register(self) -> None:
        """CLI command: register one reporter source."""
        entry = self._collect_submission_entry()
        if entry is None:
            return
        entry_id = uuid4().int % 900_000_000 + 100_000_000
        created_at = time.time_ns()
        report_key = entry["tax_report_key"]
        if entry["report_kind"] == "files":
            file_path = cast(Path, entry["tax_report_data"]).resolve()
            payload: dict[str, str | int] = {
                "tax_report_key": report_key,
                "path": str(file_path),
                "created_at": created_at,
            }
        else:
            api_payload = cast(ApiTaxReportData, entry["tax_report_data"])
            payload = {
                "tax_report_key": report_key,
                "query_id": api_payload["query_id"],
                "token": api_payload["token"],
                "created_at": created_at,
            }
        write_registry_entry(entry_id, payload)
        self.reset()

    def ls(self) -> None:
        """CLI command: list registered reporter sources."""
        entries = _load_registered_entries()
        rows = [
            [
                _entry_id_label(entry["entry_id"]),
                entry["report_title"],
                _format_submission_details(
                    {
                        "tax_report_key": entry["tax_report_key"],
                        "report_title": entry["report_title"],
                        "report_kind": entry["report_kind"],
                        "report_cls": entry["report_cls"],
                        "tax_report_data": entry["tax_report_data"],
                    }
                ),
            ]
            for entry in entries
        ]
        table = tabulate(
            rows,
            headers=["ID", "Tax report", "Details"],
            tablefmt="simple_outline",
            disable_numparse=True,
        )
        print(table, flush=True)
        question = questionary.text("[esc to back]", erase_when_done=True)
        if hasattr(question, "__dict__"):
            setattr(question, "_block_typed_input", True)
        _ask(_bind_escape_back(question))
        _clear_last_lines(table.count("\n") + 1)

    def rm(self) -> None:
        """CLI command: remove one or more registered reporter sources."""
        entries = _load_registered_entries()

        choices: list[questionary.Choice] = []
        for entry in entries:
            details = _format_submission_details(
                {
                    "tax_report_key": entry["tax_report_key"],
                    "report_title": entry["report_title"],
                    "report_kind": entry["report_kind"],
                    "report_cls": entry["report_cls"],
                    "tax_report_data": entry["tax_report_data"],
                }
            )
            choices.append(
                questionary.Choice(
                    f"#{_entry_id_label(entry['entry_id'])} {entry['report_title']} ({details})",
                    entry["entry_id"],
                )
            )

        question = questionary.checkbox(
            "Select reporters to remove [esc to back]:",
            choices=choices,
            erase_when_done=True,
        )
        selected = _ask(_bind_escape_back(question))
        if selected == "__back__":
            return
        selected_ids = cast(list[int], selected)
        if not selected_ids:
            return

        selected_ids_set = set(selected_ids)
        removed_any = False
        for entry in entries:
            if entry["entry_id"] not in selected_ids_set:
                continue
            entry["registry_path"].unlink()
            removed_any = True
        if removed_any:
            self.reset()

    def report(self) -> None:
        """CLI command: prepare summary for registered reporter sources."""
        entries = _load_registered_entries()
        if not entries:
            print("No registered tax reporters.", flush=True)
            return
        tax_report_entries: list[TaxReportEntry] = [
            {
                "tax_report_key": entry["tax_report_key"],
                "report_title": entry["report_title"],
                "report_kind": entry["report_kind"],
                "report_cls": entry["report_cls"],
                "tax_report_data": entry["tax_report_data"],
            }
            for entry in entries
        ]
        stop_event = threading.Event()
        loader_thread = threading.Thread(
            target=_run_prepare_animation,
            args=(stop_event,),
            daemon=True,
        )
        tax_report: TaxReport | None = None
        report_error: Exception | None = None
        self.report_messages = []
        with _disable_tty_input_echo():
            loader_thread.start()
            try:
                tax_report = self._build_tax_report(tax_report_entries)
            except (
                ArithmeticError,
                AssertionError,
                AttributeError,
                BufferError,
                EOFError,
                ImportError,
                LookupError,
                MemoryError,
                NameError,
                OSError,
                ReferenceError,
                RuntimeError,
                SyntaxError,
                SystemError,
                TypeError,
                UnicodeError,
                ValueError,
            ) as error:
                report_error = error
            finally:
                stop_event.set()
                loader_thread.join()
        if report_error is not None:
            self._print_error_frame(report_error)
            question = questionary.text("[esc to back]", erase_when_done=True)
            if hasattr(question, "__dict__"):
                setattr(question, "_block_typed_input", True)
            _ask(_bind_escape_back(question))
            return
        if tax_report is None:
            return
        self.tax_report = tax_report
        self.show_report()

    def show_report(self) -> None:
        """CLI command: display last prepared in-session report summary."""
        if self.tax_report is None:
            return
        self._print_tax_summary()
        question = questionary.text("[esc to back]", erase_when_done=True)
        if hasattr(question, "__dict__"):
            setattr(question, "_block_typed_input", True)
        _ask(_bind_escape_back(question))
        self.pending_full_clear = True

    def reset(self) -> None:
        """Reset in-session prepared report cache."""
        self.tax_report = None
        self.report_messages = []

    def _prompt_main_action(self) -> MenuAction:
        """Prompt for command action with conditional disabled states."""
        has_registries = bool(_load_registered_entries())
        has_session_report = self.tax_report is not None
        action = _ask(
            questionary.select(
                "Polish PIT Calculator",
                choices=[
                    questionary.Choice("Register tax reporter", "register"),
                    questionary.Choice(
                        "List tax reporters",
                        "ls",
                        disabled=None if has_registries else "No registered tax reporters",
                    ),
                    questionary.Choice(
                        "Remove tax reporters",
                        "rm",
                        disabled=None if has_registries else "No registered tax reporters",
                    ),
                    questionary.Choice(
                        "Prepare tax report",
                        "report",
                        disabled=None if has_registries else "No registered tax reporters",
                    ),
                    questionary.Choice(
                        "Show tax report",
                        "show",
                        disabled=(
                            None if has_session_report else "No prepared report in this session"
                        ),
                    ),
                    questionary.Choice("Exit", "exit"),
                ],
                erase_when_done=True,
            )
        )
        return cast(MenuAction, action)

    def _select_report_spec(self) -> ReportSpec | BackAction:
        """Prompt for report type and return either report spec or back."""
        question = questionary.select(
            "Select tax report type [esc to back]:",
            choices=[questionary.Choice(spec[1], spec) for spec in _report_specs()],
            erase_when_done=True,
        )
        value = _ask(_bind_escape_back(question))
        if value == "__back__":
            return "__back__"
        return cast(ReportSpec, value)

    def _collect_submission_entry(self) -> TaxReportEntry | None:
        """Collect one complete submission entry or return to main menu."""
        while True:
            report_spec_or_back = self._select_report_spec()
            if report_spec_or_back == "__back__":
                return None

            report_key, report_title, report_kind, report_cls = report_spec_or_back
            if report_kind == "files":
                entry = self._collect_file_entry(report_key, report_title, report_cls)
            else:
                entry = self._collect_api_entry(report_key, report_title, report_cls)

            if entry is not None:
                return entry

    def _collect_file_entry(
        self,
        report_key: str,
        report_title: str,
        report_cls: ReporterFactory,
    ) -> TaxReportEntry | None:
        """Collect one file-based report submission from user input."""
        existing_registered_paths = {
            cast(Path, entry["tax_report_data"]).resolve()
            for entry in _load_registered_entries()
            if entry["report_kind"] == "files" and entry["tax_report_key"] == report_key
        }

        def _is_already_registered(path: Path) -> bool:
            """Check whether file path is already registered for report key."""
            return path.resolve() in existing_registered_paths

        def _validate_file_input(raw: str) -> bool | str:
            """Validate non-empty, existing and extension-matching file path."""
            if not (text := raw.strip()):
                return "This field is required."
            if not (path := Path(text)).is_file():
                return "Path must be a file."
            if (report_validation := getattr(report_cls, "validate_file_path")(path)) is not True:
                return report_validation
            if _is_already_registered(path):
                return "File already registered for this report type."
            return True

        question = questionary.text(
            f"Select {report_title} file [esc to back]:",
            validate=_validate_file_input,
            completer=GreatUXPathCompleter(
                file_filter=lambda raw: (
                    (path := Path(raw)).is_dir()
                    or (
                        path.is_file()
                        and getattr(report_cls, "validate_file_path")(path) is True
                        and not _is_already_registered(path)
                    )
                ),
                expanduser=True,
            ),
            erase_when_done=True,
        )
        value = _ask(_bind_escape_back(question))
        if value == "__back__":
            return None
        path = Path(cast(str, value).strip()).resolve()
        return {
            "tax_report_key": report_key,
            "report_title": report_title,
            "report_kind": "files",
            "report_cls": report_cls,
            "tax_report_data": path,
        }

    def _collect_api_entry(
        self,
        report_key: str,
        report_title: str,
        report_cls: ReporterFactory,
    ) -> TaxReportEntry | None:
        """Collect one API-based report submission from user input."""

        def _validate_query_id(value: str) -> bool | str:
            """Require a non-empty numeric Query ID string."""
            if not (query_id := value.strip()):
                return "Query ID is required."
            if not query_id.isdigit():
                return "Query ID must be an integer."
            return True

        def _validate_token(value: str) -> bool | str:
            """Require non-empty API token input."""
            return bool(value.strip()) or "This field is required."

        query_id_question = questionary.text(
            f"{report_title} Query ID [esc to back]:",
            validate=_validate_query_id,
            erase_when_done=True,
        )
        query_value = _ask(_bind_escape_back(query_id_question))
        if query_value == "__back__":
            return None

        token_question = questionary.text(
            f"{report_title} API Token [esc to back]:",
            validate=_validate_token,
            is_password=True,
            erase_when_done=True,
        )
        token_value = _ask(_bind_escape_back(token_question))
        if token_value == "__back__":
            return None

        return {
            "tax_report_key": report_key,
            "report_title": report_title,
            "report_kind": "api",
            "report_cls": report_cls,
            "tax_report_data": {
                "query_id": str(int(cast(str, query_value).strip())),
                "token": cast(str, token_value).strip(),
            },
        }

    def _build_tax_report(self, entries: list[TaxReportEntry]) -> TaxReport:
        """Aggregate provided entries into one TaxReport object."""
        tax_report = TaxReport()
        grouped_file_entries: dict[str, GroupedFileEntry] = {}

        for tax_report_entry in entries:
            if tax_report_entry["report_kind"] == "files":
                report_key = tax_report_entry["tax_report_key"]
                group = grouped_file_entries.setdefault(
                    report_key,
                    {
                        "report_cls": tax_report_entry["report_cls"],
                        "report_title": tax_report_entry["report_title"],
                        "paths": [],
                    },
                )
                group["paths"].append(cast(Path, tax_report_entry["tax_report_data"]))
                continue

            config = cast(ApiTaxReportData, tax_report_entry["tax_report_data"])
            tax_reporter = tax_report_entry["report_cls"](config["query_id"], config["token"])
            tax_report += tax_reporter.generate()
            self.report_messages.extend(
                (tax_report_entry["report_title"], message)
                for message in tax_reporter.alignment_change_log
            )

        for grouped in grouped_file_entries.values():
            tax_reporter = grouped["report_cls"](*grouped["paths"])
            tax_report += tax_reporter.generate()
            self.report_messages.extend(
                (grouped["report_title"], message) for message in tax_reporter.alignment_change_log
            )

        return tax_report

    def _print_tax_summary(self) -> int:
        """Render cached tax summary table and return printed line count."""
        output_lines = 0
        if self.report_messages:
            for source, raw_line in sorted(
                self.report_messages,
                key=lambda entry: datetime.strptime(
                    entry[1].split(" ", 1)[0],
                    "%m/%d/%Y",
                ),
            ):
                tx_date, details = raw_line.partition(" ")[::2]
                print(
                    f"[\x1b[36m{source}\x1b[0m] [\x1b[94m{tx_date}\x1b[0m]",
                    flush=True,
                )
                output_lines += 1
                for change in filter(None, details.split("; ")):
                    print(f"  - {change}", flush=True)
                    output_lines += 1
        df = cast(TaxReport, self.tax_report).to_dataframe().copy()
        year_columns = list(df.columns[1:])
        for column in year_columns:
            df[column] = df[column].map(
                lambda value: f"{value:,.2f}" if isinstance(value, Real) else str(value)
            )
        colalign = tuple(["left", "left", *(["right"] * len(year_columns))])
        text = tabulate(
            df,
            headers="keys",
            tablefmt="simple_outline",
            showindex=True,
            disable_numparse=True,
            colalign=colalign,
        )
        print(text, flush=True)
        return output_lines + text.count("\n") + 1

    def _print_error_frame(self, error: Exception) -> None:
        """Print red traceback inside a frame and track rendered line count."""
        traceback_text = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ).rstrip("\n")
        body_lines = ["Prepare report failed:"] + traceback_text.splitlines()
        width = max(len(line) for line in body_lines)
        framed_lines = [
            f"┌{'─' * (width + 2)}┐",
            *[f"│ {line.ljust(width)} │" for line in body_lines],
            f"└{'─' * (width + 2)}┘",
        ]
        for line in framed_lines:
            print(f"\x1b[31m{line}\x1b[0m", flush=True)
        self.pending_clear_lines = len(framed_lines)


def main() -> None:
    """CLI entrypoint with clean Ctrl-C exit code."""
    app_instance = PolishPitConsoleApp()
    try:
        app_instance.run()
    except KeyboardInterrupt:
        app_instance.reset()
        sys.exit(0)


if __name__ == "__main__":
    main()
