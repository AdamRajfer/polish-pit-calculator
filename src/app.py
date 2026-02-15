from io import BytesIO
from pathlib import Path
import sys
import threading
import time
from numbers import Real
from typing import Generator, Literal, TypeAlias, TypedDict, cast
import contextlib
import os

try:
    import termios
except ImportError:
    termios = None

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
import questionary
from questionary.question import Question
from questionary.prompts.path import GreatUXPathCompleter
from tabulate import tabulate

from src.coinbase import CoinbaseTaxReporter
from src.config import TaxReport, TaxReporter
from src.ib import IBTradeCashTaxReporter
from src.ib_flex_query import IBFlexQueryTaxReporter
from src.raw import RawTaxReporter
from src.revolut import RevolutInterestTaxReporter
from src.schwab import SchwabEmployeeSponsoredTaxReporter


ReportKind: TypeAlias = Literal["files", "api"]
MenuAction: TypeAlias = Literal["submit", "prepare", "exit"]
PostSummaryAction: TypeAlias = Literal["start_over", "exit"]
BackAction: TypeAlias = Literal["__back__"]
SUBMISSION_LEFT_WIDTH = 46
SUBMISSION_RIGHT_WIDTH = SUBMISSION_LEFT_WIDTH
ReportSpec: TypeAlias = tuple[
    str,
    str,
    ReportKind,
    type[TaxReporter],
]


class ApiTaxReportData(TypedDict):
    query_id: str
    token: str


class TaxReportEntry(TypedDict):
    tax_report_key: str
    report_title: str
    report_kind: ReportKind
    report_cls: type[TaxReporter]
    tax_report_data: Path | ApiTaxReportData


class GroupedFileEntry(TypedDict):
    report_cls: type[TaxReporter]
    paths: list[Path]


def _ask(question: Question):
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

    question.application.key_bindings = merge_key_bindings([
        question.application.key_bindings,
        escape_bindings,
    ])
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


def _submission_table_line_count(entries_count: int) -> int:
    """Return number of lines occupied by the submission table."""
    return entries_count + 4 if entries_count else 0


def _clear_last_lines(lines: int) -> None:
    """Clear the last N terminal lines produced by the app."""
    if lines <= 0:
        return
    for _ in range(lines):
        sys.stdout.write("\x1b[1A\x1b[2K")
    sys.stdout.write("\r")
    sys.stdout.flush()


def _print_submission_line(
    index: int,
    report_title: str,
    details: str,
) -> None:
    """Print or extend the submitted-reports table."""
    headers = [
        "Tax report".ljust(SUBMISSION_LEFT_WIDTH),
        "Details".ljust(SUBMISSION_RIGHT_WIDTH),
    ]
    row = [
        _clip(f"#{index} {report_title}", SUBMISSION_LEFT_WIDTH).ljust(
            SUBMISSION_LEFT_WIDTH
        ),
        _clip(details, SUBMISSION_RIGHT_WIDTH).ljust(
            SUBMISSION_RIGHT_WIDTH
        ),
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


@contextlib.contextmanager
def _disable_tty_input_echo() -> Generator[None, None, None]:
    """Disable terminal input echo to avoid loader line corruption."""
    if termios is None or not os.isatty(sys.stdin.fileno()):
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


def _prompt_main_action(entries_count: int) -> MenuAction:
    """Prompt for the top-level action in the app."""
    label = (
        "Prepare tax summary"
        if not entries_count
        else (
            "Prepare tax summary ("
            f"{entries_count} tax report"
            f"{'' if entries_count == 1 else 's'})"
        )
    )
    action = _ask(questionary.select(
        "Polish PIT Calculator",
        choices=[
            questionary.Choice("Submit tax report", "submit"),
            questionary.Choice(
                label,
                "prepare",
                disabled=(
                    "Submit at least one tax report first"
                    if not entries_count
                    else None
                ),
            ),
            questionary.Choice("Exit", "exit"),
        ],
        erase_when_done=True,
    ))
    return cast(MenuAction, action)


def _select_report_spec() -> ReportSpec | BackAction:
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


def _collect_file_entry(
    report_key: str,
    report_title: str,
    report_cls: type[TaxReporter],
    entries: list[TaxReportEntry],
) -> TaxReportEntry | None:
    """Collect one file-based report submission from user input."""
    def _is_duplicate_file(path: Path) -> bool:
        """Check whether path is already submitted for this report key."""
        resolved = path.resolve()
        return any(
            submitted["report_kind"] == "files"
            and submitted["tax_report_key"] == report_key
            and cast(Path, submitted["tax_report_data"]).resolve()
            == resolved
            for submitted in entries
        )

    def _validate_file_input(raw: str) -> bool | str:
        """Validate non-empty, existing, CSV and unique path."""
        if not (text := raw.strip()):
            return "This field is required."
        if not (path := Path(text)).is_file():
            return "Path must be a file."
        if path.suffix.lower() != ".csv":
            return "Only .csv files are supported."
        if _is_duplicate_file(path):
            return "File already submitted for this report type."
        return True

    def _file_filter_input(raw: str) -> bool:
        """Show dirs plus eligible CSV files in completer."""
        if (path := Path(raw)).is_dir():
            return True
        return raw.lower().endswith(".csv") and not _is_duplicate_file(path)

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
    raw = cast(str, value)
    path = Path(raw.strip()).resolve()
    return {
        "tax_report_key": report_key,
        "report_title": report_title,
        "report_kind": "files",
        "report_cls": report_cls,
        "tax_report_data": path,
    }


def _collect_api_entry(
    report_key: str,
    report_title: str,
    report_cls: type[TaxReporter],
) -> TaxReportEntry | None:
    """Collect one API-based report submission from user input."""
    def _validate_query_id(value: str) -> bool | str:
        """Require a non-empty numeric Query ID string."""
        v = value.strip()
        if not v:
            return "Query ID is required."
        if not v.isdigit():
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
    query_id = cast(str, query_value)

    token_question = questionary.text(
        "Interactive Brokers Flex API Token [esc to back]:",
        validate=_validate_token,
        is_password=True,
        erase_when_done=True,
    )
    token_value = _ask(_bind_escape_back(token_question))
    if token_value == "__back__":
        return None
    token = cast(str, token_value)

    return {
        "tax_report_key": report_key,
        "report_title": report_title,
        "report_kind": "api",
        "report_cls": report_cls,
        "tax_report_data": {
            "query_id": str(int(query_id.strip())),
            "token": token.strip(),
        },
    }


def _collect_submission_entry(
    entries: list[TaxReportEntry],
) -> TaxReportEntry | None:
    """Collect one complete submission entry or return to main menu."""
    while True:
        report_spec_or_back = _select_report_spec()
        if report_spec_or_back == "__back__":
            return None

        report_key, report_title, report_kind, report_cls = (
            report_spec_or_back
        )
        if report_kind == "files":
            entry = _collect_file_entry(
                report_key,
                report_title,
                report_cls,
                entries,
            )
        else:
            entry = _collect_api_entry(
                report_key,
                report_title,
                report_cls,
            )
        if entry is not None:
            return entry


def _submission_details(entry: TaxReportEntry) -> str:
    """Build one-line details for submitted report entry."""
    if entry["report_kind"] == "files":
        path = cast(Path, entry["tax_report_data"])
        return f"File: {path.name}"
    query_id = cast(ApiTaxReportData, entry["tax_report_data"])["query_id"]
    return f"Query ID: {query_id}"


def _build_tax_report(entries: list[TaxReportEntry]) -> TaxReport:
    """Aggregate all submitted entries into one TaxReport object."""
    tax_report = TaxReport()
    grouped_file_entries: dict[str, GroupedFileEntry] = {}

    for tax_report_entry in entries:
        if tax_report_entry["report_kind"] == "files":
            report_key = tax_report_entry["tax_report_key"]
            group = grouped_file_entries.setdefault(
                report_key,
                {
                    "report_cls": tax_report_entry["report_cls"],
                    "paths": [],
                },
            )
            path = cast(Path, tax_report_entry["tax_report_data"])
            group["paths"].append(path)
            continue

        config = cast(ApiTaxReportData, tax_report_entry["tax_report_data"])
        tax_reporter = tax_report_entry["report_cls"](
            config["query_id"],
            config["token"],
        )
        tax_report += tax_reporter.generate()

    for grouped in grouped_file_entries.values():
        tax_reporter = grouped["report_cls"](
            *[BytesIO(path.read_bytes()) for path in grouped["paths"]]
        )
        tax_report += tax_reporter.generate()

    return tax_report


def _print_tax_summary(tax_report: TaxReport) -> int:
    """Render the final tax summary table and return printed line count."""
    df = tax_report.to_dataframe().copy()
    year_columns = list(df.columns[1:])
    for column in year_columns:
        df[column] = df[column].map(
            lambda value: f"{value:,.2f}"
            if isinstance(value, Real)
            else str(value)
        )
    colalign = tuple(
        ["left", "left"] + ["right"] * len(year_columns)
    )
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


def _prompt_post_summary_action() -> PostSummaryAction:
    """Prompt user for the next step after summary is shown."""
    action = _ask(questionary.select(
        "Tax summary ready",
        choices=[
            questionary.Choice("Start over", "start_over"),
            questionary.Choice("Exit", "exit"),
        ],
        erase_when_done=True,
    ))
    return cast(PostSummaryAction, action)


def _run_prepare_animation(stop_event: threading.Event) -> None:
    """Render a bouncing-star loader with cycling dot suffix."""
    spinner = "|/-\\"
    bar_width = 18
    index = 0
    while not stop_event.is_set():
        bounce = index % (2 * bar_width - 2)
        position = (
            bounce
            if bounce < bar_width
            else (2 * bar_width - 2 - bounce)
        )
        bar = ["-"] * bar_width
        bar[position] = "*"
        dots = "." * (index % 3 + 1)
        dot_suffix = dots.ljust(3)
        message = (
            f"\rPreparing tax summary{dot_suffix} "
            f"{spinner[index % len(spinner)]} [{''.join(bar)}]"
        )
        sys.stdout.write(message)
        sys.stdout.flush()
        index += 1
        time.sleep(0.12)
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()


def _build_tax_report_with_loader(
    entries: list[TaxReportEntry],
) -> TaxReport:
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
            return _build_tax_report(entries)
        finally:
            stop_event.set()
            loader.join()


def main() -> None:
    """Run interactive tax report collection and final summary output."""
    entries: list[TaxReportEntry] = []

    while True:
        action = _prompt_main_action(len(entries))
        if action == "submit":
            if (entry := _collect_submission_entry(entries)) is None:
                continue
            entries.append(entry)
            _print_submission_line(
                len(entries),
                entry["report_title"],
                _submission_details(entry),
            )
            continue

        elif action == "prepare":
            if not entries:
                print("No submitted tax reports.")
                continue
            tax_report = _build_tax_report_with_loader(entries)
            _clear_last_lines(
                _submission_table_line_count(len(entries))
            )
            summary_lines = _print_tax_summary(tax_report)
            post_action = _prompt_post_summary_action()
            if post_action == "start_over":
                entries.clear()
                _clear_last_lines(summary_lines)
                continue
            sys.exit(0)

        elif action == "exit":
            sys.exit(0)


def run() -> None:
    """CLI entrypoint with clean Ctrl-C exit code."""
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()
