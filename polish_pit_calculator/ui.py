"""UI helpers for questionary prompts and terminal interaction."""

import contextlib
import functools
import os
import sys
import termios
import threading
import time
import traceback
from io import UnsupportedOperation
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Generator, Literal, ParamSpec, TypeVar, cast

import questionary
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.shortcuts import clear as prompt_toolkit_clear
from questionary.prompts.path import GreatUXPathCompleter
from questionary.question import Question
from tabulate import tabulate

from polish_pit_calculator.config import TaxReport
from polish_pit_calculator.registry import TaxReporterRegistry
from polish_pit_calculator.tax_reporters import FileTaxReporter, TaxReporter

ReporterT = TypeVar("ReporterT", bound=TaxReporter)
ParamsT = ParamSpec("ParamsT")
ResultT = TypeVar("ResultT")
MainMenuAction = Literal["register", "ls", "rm", "report", "show", "reset", "exit_app"]
BackAction = Literal["__back__"]


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


def _ask(
    question: Question,
    disable_escape_back: bool = False,
    block_typed_input: bool = False,
) -> Any:
    """Run a Questionary prompt with built-in ESC back handling."""
    if not disable_escape_back:
        escape_bindings = KeyBindings()

        @escape_bindings.add("escape", eager=True)
        def _(_event: KeyPressEvent) -> None:
            """Exit prompt immediately and return a back sentinel."""
            _event.app.exit(result="__back__")

        question.application.key_bindings = merge_key_bindings(
            [escape_bindings, question.application.key_bindings]
        )
    if block_typed_input:
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
    question.application.ttimeoutlen = 0
    question.application.timeoutlen = 0
    return question.unsafe_ask()


def clear_terminal_viewport() -> None:
    """Clear terminal viewport and scrollback, then reset cursor to top-left."""
    prompt_toolkit_clear()
    sys.stdout.write("\x1b[3J\x1b[2J\x1b[H")
    sys.stdout.flush()


def prompt_for_main_menu_action(has_prepared_report: bool) -> MainMenuAction:
    """Prompt for one main-menu action and return selected command key."""
    disabled_exec = None if TaxReporterRegistry.deserialize_all() else "No registered tax reporters"
    disabled_show = None if has_prepared_report else "No prepared report in this session"
    question = questionary.select(
        "Polish PIT Calculator",
        choices=[
            questionary.Choice("Register tax reporter", "register"),
            questionary.Choice("List tax reporters", "ls", disabled=disabled_exec),
            questionary.Choice("Remove tax reporters", "rm", disabled=disabled_exec),
            questionary.Choice("Prepare tax report", "report", disabled=disabled_exec),
            questionary.Choice("Show tax report", "show", disabled=disabled_show),
            questionary.Choice("Reset tax report", "reset", disabled=disabled_show),
            questionary.Choice("Exit", "exit_app"),
        ],
        erase_when_done=True,
    )
    return cast(MainMenuAction, _ask(question, disable_escape_back=True))


def prompt_for_tax_reporter_class() -> type[TaxReporter] | BackAction:
    """Prompt for tax reporter class selection and return class or '__back__'."""
    question = questionary.select(
        "Select tax report type [esc to back]:",
        choices=[
            questionary.Choice(class_def.name(), class_def)
            for class_def in TaxReporterRegistry.ls()
        ],
        erase_when_done=True,
    )
    return cast(type[TaxReporter] | BackAction, _ask(question))


def prompt_for_tax_reporter(reporter_cls: type[ReporterT]) -> ReporterT | None:
    """Collect constructor arguments for reporter class using its validator map."""
    payload: dict[str, str] = {}
    for attr_name, validate in reporter_cls.validators().items():
        label = attr_name.replace("_", " ").title()
        kwargs: dict[str, Any] = {"validate": validate, "erase_when_done": True}
        if issubclass(reporter_cls, FileTaxReporter):

            def _file_filter(
                raw: str,
                validate_fn: Callable[[str], bool | str] = validate,
            ) -> bool:
                path = Path(raw).expanduser().resolve()
                return path.is_dir() or (path.is_file() and validate_fn(str(path)) is True)

            kwargs["completer"] = GreatUXPathCompleter(
                file_filter=_file_filter,
                expanduser=True,
            )
        question = questionary.text(f"{label} [esc to back]:", **kwargs)
        answer = _ask(question)
        if answer == "__back__":
            return None
        payload[attr_name] = str(answer).strip()
    return cast(ReporterT, reporter_cls(**payload))


def prompt_for_entry_ids_to_remove() -> list[str] | BackAction:
    """Prompt for registered reporter IDs to remove and return selected IDs."""
    question = questionary.checkbox(
        "Select reporters to remove [esc to back]:",
        choices=[
            questionary.Choice(
                f"#{entry_id} {tax_reporter.name()} ({tax_reporter.details})",
                entry_id,
            )
            for entry_id, tax_reporter in TaxReporterRegistry.deserialize_all()
        ],
        erase_when_done=True,
    )
    return cast(list[str] | BackAction, _ask(question))


def with_prepare_animation(
    method: Callable[ParamsT, ResultT],
) -> Callable[ParamsT, ResultT]:
    """Decorator that runs method body with prepare animation and tty guard."""

    @functools.wraps(method)
    def _wrapped(*args: ParamsT.args, **kwargs: ParamsT.kwargs) -> ResultT:
        stop_event = threading.Event()

        def _run_prepare_animation() -> None:
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

        loader_thread = threading.Thread(target=_run_prepare_animation, daemon=True)
        with _disable_tty_input_echo():
            loader_thread.start()
            try:
                return method(*args, **kwargs)
            finally:
                stop_event.set()
                loader_thread.join()

    return _wrapped


def wait_for_back_navigation() -> None:
    """Display read-only back prompt and wait until user dismisses it."""
    question = questionary.text("[esc to back]", erase_when_done=True)
    _ask(question, block_typed_input=True)


def print_tax_reporters(entries: list[tuple[str, TaxReporter]]) -> None:
    """Render and print one table with registered reporter entries."""
    table = tabulate(
        [
            [entry_id, tax_reporter.name(), tax_reporter.details]
            for entry_id, tax_reporter in entries
        ],
        headers=["ID", "Tax Reporter", "Details"],
        tablefmt="simple_outline",
        disable_numparse=True,
    )
    print(table, flush=True)


def print_tax_report(tax_report: TaxReport, logs: list[str]) -> None:
    """Render prepared report output and print it."""
    df = tax_report.to_dataframe()
    year_columns = list(df.columns[1:])
    for column in year_columns:
        df[column] = df[column].map(lambda x: f"{x:,.2f}" if isinstance(x, Real) else str(x))
    table = tabulate(
        df,
        headers="keys",
        tablefmt="simple_outline",
        showindex=True,
        disable_numparse=True,
        colalign=tuple(["left", "left", *(["right"] * len(year_columns))]),
    )
    print("\n".join([*logs, table]), flush=True)


def print_tax_report_error(error: Exception) -> None:
    """Render and print framed red traceback for report-preparation errors."""
    traceback_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    ).rstrip("\n")
    error_lines = traceback_text.splitlines()
    width = max(len(line) for line in error_lines)
    framed_error = "\n".join(
        [
            f"┌{'─' * (width + 2)}┐",
            *[f"│ {line.ljust(width)} │" for line in error_lines],
            f"└{'─' * (width + 2)}┘",
        ]
    )
    print(f"\x1b[31m{framed_error}\x1b[0m", flush=True)
