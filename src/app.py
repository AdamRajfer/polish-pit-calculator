"""Interactive console application for collecting and summarizing tax reports."""

import contextlib
import os
import sys
import termios
import threading
import time
from io import BytesIO
from numbers import Real
from pathlib import Path
from typing import Callable, Generator, Literal, TypedDict, cast

import questionary
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from questionary.prompts.path import GreatUXPathCompleter
from questionary.question import Question
from tabulate import tabulate

from src.coinbase import CoinbaseTaxReporter
from src.config import TaxReport, TaxReporter
from src.ib import IBTradeCashTaxReporter
from src.ib_flex_query import IBFlexQueryTaxReporter
from src.raw import RawTaxReporter
from src.revolut import RevolutInterestTaxReporter
from src.schwab import SchwabEmployeeSponsoredTaxReporter

type ReportKind = Literal["files", "api"]
type MenuAction = Literal["submit", "prepare", "exit"]
type PostSummaryAction = Literal["start_over", "exit"]
type BackAction = Literal["__back__"]
type ReporterFactory = Callable[..., TaxReporter]
type ReportSpec = tuple[str, str, ReportKind, ReporterFactory]


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
    paths: list[Path]


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
        [question.application.key_bindings, escape_bindings]
    )
    return question


def _clip(text: str, width: int) -> str:
    """Clip text to width, using middle '...' when truncation is needed."""
    if width <= 3:
        return text[:width]
    if len(text) <= width:
        return text
    available = width - 3
    left = (available + 1) // 2
    right = available - left
    if right == 0:
        return f"{text[:left]}..."
    return f"{text[:left]}...{text[-right:]}"


def _clear_last_lines(lines: int) -> None:
    """Clear the last N terminal lines produced by the app."""
    if lines <= 0:
        return
    for _ in range(lines):
        sys.stdout.write("\x1b[1A\x1b[2K")
    sys.stdout.write("\r")
    sys.stdout.flush()


@contextlib.contextmanager
def _disable_tty_input_echo() -> Generator[None, None, None]:
    """Disable terminal input echo to avoid loader line corruption."""
    if not os.isatty(sys.stdin.fileno()):
        yield
        return

    fd = sys.stdin.fileno()
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
        """Initialize in-memory submitted entries collection."""
        self.entries: list[TaxReportEntry] = []

    def run(self) -> None:
        """Run interactive tax report collection and final summary output."""
        while True:
            action = self._prompt_main_action()

            if action == "submit":
                if (entry := self._collect_submission_entry()) is None:
                    continue
                self.entries.append(entry)
                self._print_last_submission(entry)
                continue

            if action == "prepare":
                tax_report = self._build_tax_report_with_loader()
                self._clear_submission_table()
                summary_lines = self._print_tax_summary(tax_report)
                if self._prompt_post_summary_action() == "start_over":
                    self.reset()
                    _clear_last_lines(summary_lines)
                    continue
                sys.exit(0)

            if action == "exit":
                sys.exit(0)

    def reset(self) -> None:
        """Clear all submitted entries."""
        self.entries.clear()

    def _prompt_main_action(self) -> MenuAction:
        """Prompt for app action based on current entry count."""
        entries_count = len(self.entries)
        label = (
            "Prepare tax summary"
            if not entries_count
            else f"Prepare tax summary ({entries_count} tax report"
            f"{'' if entries_count == 1 else 's'})"
        )
        action = _ask(
            questionary.select(
                "Polish PIT Calculator",
                choices=[
                    questionary.Choice("Submit tax report", "submit"),
                    questionary.Choice(
                        label,
                        "prepare",
                        disabled=(
                            "Submit at least one tax report first" if not entries_count else None
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
            choices=[
                questionary.Choice(
                    "Charles Schwab Employee Sponsored",
                    (
                        "schwab_employee_sponsored",
                        "Charles Schwab Employee Sponsored",
                        "files",
                        SchwabEmployeeSponsoredTaxReporter,
                    ),
                ),
                questionary.Choice(
                    "Interactive Brokers Trade Cash",
                    (
                        "ib_trade_cash",
                        "Interactive Brokers Trade Cash",
                        "files",
                        IBTradeCashTaxReporter,
                    ),
                ),
                questionary.Choice(
                    "Interactive Brokers Flex Query",
                    (
                        "ib_flex_query",
                        "Interactive Brokers Flex Query",
                        "api",
                        IBFlexQueryTaxReporter,
                    ),
                ),
                questionary.Choice(
                    "Coinbase Crypto",
                    (
                        "coinbase_crypto",
                        "Coinbase Crypto",
                        "files",
                        CoinbaseTaxReporter,
                    ),
                ),
                questionary.Choice(
                    "Revolut Interest",
                    (
                        "revolut_interest",
                        "Revolut Interest",
                        "files",
                        RevolutInterestTaxReporter,
                    ),
                ),
                questionary.Choice(
                    "Raw Custom CSV",
                    (
                        "raw_custom_csv",
                        "Raw Custom CSV",
                        "files",
                        RawTaxReporter,
                    ),
                ),
            ],
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

    def _is_duplicate_file(self, report_key: str, path: Path) -> bool:
        """Check whether path is already submitted for this report key."""
        resolved = path.resolve()
        return any(
            submitted["report_kind"] == "files"
            and submitted["tax_report_key"] == report_key
            and cast(Path, submitted["tax_report_data"]).resolve() == resolved
            for submitted in self.entries
        )

    def _collect_file_entry(
        self,
        report_key: str,
        report_title: str,
        report_cls: ReporterFactory,
    ) -> TaxReportEntry | None:
        """Collect one file-based report submission from user input."""

        def _validate_file_input(raw: str) -> bool | str:
            """Validate non-empty, existing, CSV and unique path."""
            if not (text := raw.strip()):
                return "This field is required."
            if not (path := Path(text)).is_file():
                return "Path must be a file."
            if path.suffix.lower() != ".csv":
                return "Only .csv files are supported."
            if self._is_duplicate_file(report_key, path):
                return "File already submitted for this report type."
            return True

        def _file_filter_input(raw: str) -> bool:
            """Show dirs plus eligible CSV files in completer."""
            if (path := Path(raw)).is_dir():
                return True
            return raw.lower().endswith(".csv") and not self._is_duplicate_file(
                report_key,
                path,
            )

        question = questionary.text(
            f"Select {report_title} file [esc to back]:",
            validate=_validate_file_input,
            completer=GreatUXPathCompleter(
                file_filter=_file_filter_input,
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
            "Interactive Brokers Flex Query ID [esc to back]:",
            validate=_validate_query_id,
            erase_when_done=True,
        )
        query_value = _ask(_bind_escape_back(query_id_question))
        if query_value == "__back__":
            return None

        token_question = questionary.text(
            "Interactive Brokers Flex API Token [esc to back]:",
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

    def _submission_details(self, entry: TaxReportEntry) -> str:
        """Build one-line details for submitted report entry."""
        if entry["report_kind"] == "files":
            path = cast(Path, entry["tax_report_data"])
            return f"File: {path.name}"
        query_id = cast(ApiTaxReportData, entry["tax_report_data"])["query_id"]
        return f"Query ID: {query_id}"

    def _print_submission_line(
        self,
        index: int,
        report_title: str,
        details: str,
    ) -> None:
        """Print or extend the submitted-reports table."""
        left_width = 46
        right_width = 46
        headers = ["Tax report".ljust(left_width), "Details".ljust(right_width)]
        row = [
            _clip(f"#{index} {report_title}", left_width).ljust(left_width),
            _clip(details, right_width).ljust(right_width),
        ]
        table = tabulate(
            [row],
            headers=headers,
            tablefmt="simple_outline",
            disable_numparse=True,
        )
        if index == 1:
            print(table, flush=True)
            return
        append_block = "\n".join(table.splitlines()[3:])
        sys.stdout.write("\x1b[1A\x1b[2K\r")
        sys.stdout.write(f"{append_block}\n")
        sys.stdout.flush()

    def _print_last_submission(self, entry: TaxReportEntry) -> None:
        """Print the table line for the last appended submission."""
        self._print_submission_line(
            len(self.entries),
            entry["report_title"],
            self._submission_details(entry),
        )

    def _submission_table_line_count(self) -> int:
        """Return number of lines occupied by the submission table."""
        return len(self.entries) + 4 if self.entries else 0

    def _clear_submission_table(self) -> None:
        """Clear currently printed submission table from terminal."""
        _clear_last_lines(self._submission_table_line_count())

    def _build_tax_report(self) -> TaxReport:
        """Aggregate all submitted entries into one TaxReport object."""
        tax_report = TaxReport()
        grouped_file_entries: dict[str, GroupedFileEntry] = {}

        for tax_report_entry in self.entries:
            if tax_report_entry["report_kind"] == "files":
                report_key = tax_report_entry["tax_report_key"]
                group = grouped_file_entries.setdefault(
                    report_key,
                    {
                        "report_cls": tax_report_entry["report_cls"],
                        "paths": [],
                    },
                )
                group["paths"].append(cast(Path, tax_report_entry["tax_report_data"]))
                continue

            config = cast(ApiTaxReportData, tax_report_entry["tax_report_data"])
            tax_reporter = tax_report_entry["report_cls"](config["query_id"], config["token"])
            tax_report += tax_reporter.generate()

        for grouped in grouped_file_entries.values():
            tax_reporter = grouped["report_cls"](
                *[BytesIO(path.read_bytes()) for path in grouped["paths"]]
            )
            tax_report += tax_reporter.generate()

        return tax_report

    def _build_tax_report_with_loader(self) -> TaxReport:
        """Build tax report while rendering a loader animation."""
        with _disable_tty_input_echo():
            stop_event = threading.Event()
            loader = threading.Thread(
                target=_run_prepare_animation,
                args=(stop_event,),
                daemon=True,
            )
            loader.start()
            try:
                return self._build_tax_report()
            finally:
                stop_event.set()
                loader.join()

    def _print_tax_summary(self, tax_report: TaxReport) -> int:
        """Render the final tax summary table and return printed line count."""
        df = tax_report.to_dataframe().copy()
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
        return text.count("\n") + 1

    def _prompt_post_summary_action(self) -> PostSummaryAction:
        """Prompt user for the next step after summary is shown."""
        action = _ask(
            questionary.select(
                "Tax summary ready",
                choices=[
                    questionary.Choice("Start over", "start_over"),
                    questionary.Choice("Exit", "exit"),
                ],
                erase_when_done=True,
            )
        )
        return cast(PostSummaryAction, action)


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
