"""Abstract base class for all tax reporters."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from polish_pit_calculator.config import LogChange, PromptValidator, TaxReport, TaxReportLogs


class TaxReporter(ABC):
    """Abstract base class for all tax reporters."""

    @classmethod
    @abstractmethod
    def name(cls) -> str:
        """Return reporter name shown in app choices."""

    @classmethod
    @abstractmethod
    def validators(cls) -> dict[str, PromptValidator]:
        """Return constructor-attribute validators used by prompt builder."""

    @property
    @abstractmethod
    def details(self) -> str:
        """Return one-line details string shown in registry views."""

    @abstractmethod
    def generate(self, logs: TaxReportLogs | None = None) -> TaxReport:
        """Build and return yearly tax report data."""

    @abstractmethod
    def to_entry_data(self) -> dict[str, Any]:
        """Build reporter-specific payload persisted under entry `data` key."""

    def update_logs(
        self,
        log_date: date,
        action: str,
        detail: str,
        changes: list[LogChange],
        logs: TaxReportLogs,
    ) -> None:
        """Insert one reporter log line into shared sink in ascending date order."""
        header = (
            f"[\x1b[36m{self.name()}\x1b[0m] "
            f"[\x1b[95m{log_date.strftime('%m/%d/%Y')}\x1b[0m] "
            f"[\x1b[33m{action} {detail}\x1b[0m]"
        )
        changes_text = "\n".join(
            (
                f" \x1b[36m•\x1b[0m {change['name']}: "
                f"\x1b[31m{change['before']}\x1b[0m -> "
                f"\x1b[32m{change['after']}\x1b[0m"
            )
            for change in changes
        )
        logs.add(log_date, f"{header}\n{changes_text}")
