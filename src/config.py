"""Core tax data models shared by all tax reporters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from pathlib import Path

import pandas as pd


@dataclass(frozen=True, slots=True)
class TaxRecord:
    """Yearly tax record with calculated helper properties."""

    trade_revenue: float = 0.0
    trade_cost: float = 0.0
    trade_loss_from_previous_years: float = 0.0
    crypto_revenue: float = 0.0
    crypto_cost: float = 0.0
    crypto_cost_excess_from_previous_years: float = 0.0
    domestic_interest: float = 0.0
    foreign_interest: float = 0.0
    foreign_interest_withholding_tax: float = 0.0
    employment_revenue: float = 0.0
    employment_cost: float = 0.0
    social_security_contributions: float = 0.0
    donations: float = 0.0

    def __eq__(self, other: object) -> bool:
        """Compare two tax records field by field."""
        if not isinstance(other, TaxRecord):
            return False
        return all(
            getattr(self, field_info.name) == getattr(other, field_info.name)
            for field_info in fields(TaxRecord)
        )

    def __add__(self, other: "TaxRecord") -> "TaxRecord":
        """Add two tax records field by field."""
        kwargs = {
            field_info.name: getattr(self, field_info.name) + getattr(other, field_info.name)
            for field_info in fields(TaxRecord)
        }
        return TaxRecord(**kwargs)

    @property
    def trade_profit(self) -> float:
        """Return positive trade result after carry-over losses."""
        amount = self.trade_revenue - self.trade_cost - self.trade_loss_from_previous_years
        return amount if amount > 0.0 else 0.0

    @property
    def trade_loss(self) -> float:
        """Return trade loss amount to carry to next year."""
        amount = self.trade_revenue - self.trade_cost - self.trade_loss_from_previous_years
        return -amount if amount < 0.0 else 0.0

    @property
    def trade_tax(self) -> float:
        """Return 19 percent tax due on taxable trade profit."""
        return self.trade_profit * 0.19

    @property
    def crypto_profit(self) -> float:
        """Return positive crypto result after previous cost excess."""
        amount = (
            self.crypto_revenue - self.crypto_cost - self.crypto_cost_excess_from_previous_years
        )
        return amount if amount > 0.0 else 0.0

    @property
    def crypto_cost_excess(self) -> float:
        """Return crypto cost excess to carry to next year."""
        amount = (
            self.crypto_revenue - self.crypto_cost - self.crypto_cost_excess_from_previous_years
        )
        return -amount if amount < 0.0 else 0.0

    @property
    def crypto_tax(self) -> float:
        """Return 19 percent tax due on taxable crypto profit."""
        return self.crypto_profit * 0.19

    @property
    def domestic_interest_tax(self) -> float:
        """Return tax due on domestic interest income."""
        return self.domestic_interest * 0.19

    @property
    def foreign_interest_tax(self) -> float:
        """Return tax due on foreign interest income."""
        return self.foreign_interest * 0.19

    @property
    def foreign_interest_remaining_tax(self) -> float:
        """Return payable foreign interest tax after withholding."""
        return max(
            self.foreign_interest_tax - self.foreign_interest_withholding_tax,
            0.0,
        )

    @property
    def employment_profit(self) -> float:
        """Return employment profit before tax deductions."""
        return self.employment_revenue - self.employment_cost

    @property
    def employment_profit_deduction(self) -> float:
        """Return deductible employment-profit amount."""
        return min(0.06 * self.employment_profit, self.donations)

    @property
    def total_profit(self) -> float:
        """Return total taxable profit across categories."""
        return self.employment_profit + self.trade_profit + self.crypto_profit

    @property
    def total_profit_deductions(self) -> float:
        """Return total deductions reducing solidarity-tax base."""
        return self.employment_profit_deduction + self.social_security_contributions

    @property
    def solidarity_tax(self) -> float:
        """Return solidarity tax due above the statutory threshold."""
        return max(self.total_profit - self.total_profit_deductions - 1e6, 0.0) * 0.04

    @property
    def total_tax(self) -> float:
        """Return total payable tax from all supported categories."""
        return (
            self.trade_tax
            + self.crypto_tax
            + self.domestic_interest_tax
            + self.foreign_interest_remaining_tax
            + self.solidarity_tax
        )

    def to_dict(self) -> dict[str, float]:
        """Serialize the record to report-row labels and numeric values."""
        return {
            "Trade Revenue": self.trade_revenue,
            "Trade Cost": self.trade_cost,
            "Trade Loss from Previous Years": self.trade_loss_from_previous_years,
            "Trade Loss": self.trade_loss,
            "Crypto Revenue": self.crypto_revenue,
            "Crypto Cost": self.crypto_cost,
            "Crypto Cost Excess from Previous Years": self.crypto_cost_excess_from_previous_years,
            "Crypto Cost Excess": self.crypto_cost_excess,
            "Domestic Interest Tax": self.domestic_interest_tax,
            "Foreign Interest Tax": self.foreign_interest_tax,
            "Foreign Interest Withholding Tax": self.foreign_interest_withholding_tax,
            "Employment Profit Deduction": self.employment_profit_deduction,
            "Total Profit": self.total_profit,
            "Total Profit Deductions": self.total_profit_deductions,
            "Solidarity Tax": self.solidarity_tax,
            "Total Tax": self.total_tax,
        }

    @staticmethod
    def get_name_to_pit_label_mapping() -> dict[str, str]:
        """Map output row names to PIT form coordinates."""
        return {
            "Trade Revenue": "PIT-38/C20",
            "Trade Cost": "PIT-38/C21",
            "Trade Loss from Previous Years": "PIT-38/D28",
            "Trade Loss": "PIT-38/D28 - Next Year",
            "Crypto Revenue": "PIT-38/E34",
            "Crypto Cost": "PIT-38/E35",
            "Crypto Cost Excess from Previous Years": "PIT-38/E36",
            "Crypto Cost Excess": "PIT-38/E36 - Next Year",
            "Domestic Interest Tax": "PIT-38/G44",
            "Foreign Interest Tax": "PIT-38/G45",
            "Foreign Interest Withholding Tax": "PIT-38/G46",
            "Employment Profit Deduction": "PIT/O/B11 -> PIT-37/F124",
            "Total Profit": "DSF-1/C18 - If Solidarity Tax > 0.00",
            "Total Profit Deductions": "DSF-1/C19 - If Solidarity Tax > 0.00",
            "Solidarity Tax": "",
            "Total Tax": "",
        }


@dataclass(frozen=True)
class TaxReport:
    """Collection of yearly tax records with merge and display helpers."""

    year_to_tax_record: dict[int, TaxRecord] = field(default_factory=dict)

    def __add__(self, other: "TaxReport") -> "TaxReport":
        """Merge two reports by summing records for matching years."""
        tax_report = TaxReport()
        for year in set(self.year_to_tax_record).union(other.year_to_tax_record):
            if year not in self.year_to_tax_record:
                tax_report[year] = other[year]
            elif year not in other.year_to_tax_record:
                tax_report[year] = self[year]
            else:
                tax_report[year] = self[year] + other[year]
        return tax_report

    def __getitem__(self, year: int) -> TaxRecord:
        """Return a year record, defaulting to an empty TaxRecord."""
        return self.year_to_tax_record.get(year, TaxRecord())

    def __setitem__(self, year: int, tax_record: TaxRecord) -> None:
        """Register a tax record for a year exactly once."""
        if year in self.year_to_tax_record:
            raise ValueError(f"Tax record for year {year} already registered.")
        self.year_to_tax_record[year] = tax_record

    def items(self) -> list[tuple[int, TaxRecord]]:
        """Return year-record pairs."""
        return list(self.year_to_tax_record.items())

    def to_dataframe(self) -> pd.DataFrame:
        """Convert report to a tabular dataframe with PIT labels."""
        pit_label_df = pd.Series(
            TaxRecord.get_name_to_pit_label_mapping(),
            name="PIT",
        ).to_frame()
        df = (
            pd.DataFrame.from_dict(
                {k: v.to_dict() for k, v in self.items()},
                orient="index",
            )
            .T.sort_index(axis=1)
            .map(lambda value: f"{value:,.2f}")
        )
        return pit_label_df.join(df)


class TaxReporter(ABC):
    """Abstract base class for all tax reporters."""

    def __init__(self) -> None:
        """Initialize optional alignment log shared by reporters."""
        self.alignment_change_log: list[str] = []

    @abstractmethod
    def generate(self) -> TaxReport:
        """Build and return yearly tax report data."""

    @classmethod
    def validate_file_path(cls, _path: Path) -> bool | str:
        """Validate reporter-specific file input path."""
        return True


class CsvTaxReporter(TaxReporter, ABC):
    """Base class for CSV-backed file reporters."""

    def __init__(self, *files: Path) -> None:
        """Store provided CSV file paths."""
        super().__init__()
        self.files = files

    @classmethod
    def validate_file_path(cls, path: Path) -> bool | str:
        """Validate CSV reporter file extension."""
        if path.suffix.lower() != ".csv":
            return "Only .csv files are supported."
        return True


class JsonTaxReporter(TaxReporter, ABC):
    """Base class for JSON-backed file reporters."""

    def __init__(self, *files: Path) -> None:
        """Store provided JSON file paths."""
        super().__init__()
        self.files = files

    @classmethod
    def validate_file_path(cls, path: Path) -> bool | str:
        """Validate JSON reporter file extension."""
        if path.suffix.lower() != ".json":
            return "Only .json files are supported."
        return True
