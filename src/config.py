from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TaxRecord:
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

    @property
    def trade_profit(self) -> float:
        amount = (
            self.trade_revenue
            - self.trade_cost
            - self.trade_loss_from_previous_years
        )
        return amount if amount > 0.0 else 0.0

    @property
    def trade_loss(self) -> float:
        amount = (
            self.trade_revenue
            - self.trade_cost
            - self.trade_loss_from_previous_years
        )
        return -amount if amount < 0.0 else 0.0

    @property
    def trade_tax(self) -> float:
        return self.trade_profit * 0.19

    @property
    def crypto_profit(self) -> float:
        amount = (
            self.crypto_revenue
            - self.crypto_cost
            - self.crypto_cost_excess_from_previous_years
        )
        return amount if amount > 0.0 else 0.0

    @property
    def crypto_cost_excess(self) -> float:
        amount = (
            self.crypto_revenue
            - self.crypto_cost
            - self.crypto_cost_excess_from_previous_years
        )
        return -amount if amount < 0.0 else 0.0

    @property
    def crypto_tax(self) -> float:
        return self.crypto_profit * 0.19

    @property
    def domestic_interest_tax(self) -> float:
        return self.domestic_interest * 0.19

    @property
    def foreign_interest_tax(self) -> float:
        return self.foreign_interest * 0.19

    @property
    def foreign_interest_remaining_tax(self) -> float:
        return max(
            self.foreign_interest_tax - self.foreign_interest_withholding_tax,
            0.0,
        )

    @property
    def employment_profit(self) -> float:
        return self.employment_revenue - self.employment_cost

    @property
    def employment_profit_deduction(self) -> float:
        return min(0.06 * self.employment_profit, self.donations)

    @property
    def total_profit(self) -> float:
        return self.employment_profit + self.trade_profit + self.crypto_profit

    @property
    def total_profit_deductions(self) -> float:
        return (
            self.employment_profit_deduction
            + self.social_security_contributions
        )

    @property
    def solidarity_tax(self) -> float:
        return (
            max(self.total_profit - self.total_profit_deductions - 1e6, 0.0)
            * 0.04
        )

    @property
    def total_tax(self) -> float:
        return (
            self.trade_tax
            + self.crypto_tax
            + self.domestic_interest_tax
            + self.foreign_interest_remaining_tax
            + self.solidarity_tax
        )

    def to_dict(self) -> dict[str, float]:
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

    def __add__(self, other: "TaxRecord") -> "TaxRecord":
        kwargs = {
            field: getattr(self, field) + getattr(other, field)
            for field in TaxRecord.__dataclass_fields__
        }
        return TaxRecord(**kwargs)


@dataclass(frozen=True)
class TaxReport:
    year_to_tax_record: dict[int, TaxRecord] = field(default_factory=dict)

    def __add__(self, other: "TaxReport") -> "TaxReport":
        tax_report = TaxReport()
        for year in set(self.year_to_tax_record).union(
            other.year_to_tax_record
        ):
            if year not in self.year_to_tax_record:
                tax_report[year] = other[year]
            elif year not in other.year_to_tax_record:
                tax_report[year] = self[year]
            else:
                tax_report[year] = self[year] + other[year]
        return tax_report

    def __getitem__(self, year: int) -> TaxRecord:
        return self.year_to_tax_record.get(year, TaxRecord())

    def __setitem__(self, year: int, tax_record: TaxRecord) -> None:
        if year in self.year_to_tax_record:
            raise ValueError(f"Tax record for year {year} already registered.")
        self.year_to_tax_record[year] = tax_record

    def items(self) -> list[tuple[int, TaxRecord]]:
        return list(self.year_to_tax_record.items())

    def to_dataframe(self) -> pd.DataFrame:
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
            .map("{:,.2f}".format)
        )
        return pit_label_df.join(df)


class TaxReporter(ABC):
    def __init__(self, *args: Any) -> None:
        self.args = args

    @abstractmethod
    def generate(self) -> TaxReport:
        pass
