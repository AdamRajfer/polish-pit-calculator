"""Interactive console application for collecting and summarizing tax reports."""

import sys
from typing import cast

from polish_pit_calculator import ui
from polish_pit_calculator.config import TaxReport, TaxReportLogs
from polish_pit_calculator.registry import TaxReporterRegistry

_REPORT_PREPARE_EXCEPTIONS = (
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
)


class App:
    """Stateful interactive console app for building tax summaries."""

    def __init__(self) -> None:
        """Initialize in-session prepared report cache."""
        self.tax_report: TaxReport | None = None
        self.logs = TaxReportLogs()

    def run(self) -> None:
        """Run interactive registry command loop."""
        while True:
            ui.clear_terminal_viewport()
            main_menu_action = ui.prompt_for_main_menu_action(self.tax_report is not None)
            getattr(self, main_menu_action)()

    def register(self) -> None:
        """CLI command: register one reporter source."""
        while True:
            tax_reporter_class = ui.prompt_for_tax_reporter_class()
            if tax_reporter_class == "__back__":
                return
            tax_reporter = ui.prompt_for_tax_reporter(tax_reporter_class)
            if tax_reporter is None:
                continue
            break
        TaxReporterRegistry.serialize(tax_reporter)
        self._reset()

    def ls(self) -> None:
        """CLI command: list registered reporter sources."""
        deserialized = TaxReporterRegistry.deserialize_all()
        ui.print_tax_reporters(deserialized)
        ui.wait_for_back_navigation()

    def rm(self) -> None:
        """CLI command: remove one or more registered reporter sources."""
        entry_ids_to_remove = ui.prompt_for_entry_ids_to_remove()
        if entry_ids_to_remove == "__back__" or not entry_ids_to_remove:
            return
        for entry_id in entry_ids_to_remove:
            TaxReporterRegistry.unregister(entry_id)
        self._reset()

    def report(self) -> None:
        """CLI command: prepare summary for registered reporter sources."""
        deserialized = TaxReporterRegistry.deserialize_all()

        @ui.with_prepare_animation
        def _prepare_report() -> TaxReport:
            return cast(
                TaxReport, sum(tax_reporter.generate(self.logs) for _, tax_reporter in deserialized)
            )

        try:
            self.tax_report = _prepare_report()
            self.show()
        except _REPORT_PREPARE_EXCEPTIONS as error:
            self._reset()
            self._show_error(error)

    def show(self) -> None:
        """CLI command: display last prepared in-session report summary."""
        ui.print_tax_report(cast(TaxReport, self.tax_report), self.logs)
        ui.wait_for_back_navigation()

    def exit_app(self) -> None:
        """Exit interactive run loop."""
        self._reset()
        sys.exit(0)

    def _reset(self) -> None:
        """Reset in-session prepared report cache."""
        self.tax_report = None
        self.logs.clear()

    def _show_error(self, error: Exception) -> None:
        """CLI command: display last prepared in-session report summary."""
        ui.print_tax_report_error(error)
        ui.wait_for_back_navigation()


def main() -> None:
    """CLI entrypoint with clean Ctrl-C exit code."""
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        app.exit_app()


if __name__ == "__main__":
    main()
