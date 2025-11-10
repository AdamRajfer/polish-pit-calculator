from dataclasses import dataclass
from datetime import date
from enum import Enum, auto
from typing import Any, Type, cast

import pandas as pd
import streamlit as st

from polish_pit_calculator.coinbase import CoinbaseTaxReporter
from polish_pit_calculator.config import TaxReport, TaxReporter
from polish_pit_calculator.ib import IBTradeCashTaxReporter
from polish_pit_calculator.manual import ManualTaxReporter
from polish_pit_calculator.raw import RawTaxReporter
from polish_pit_calculator.revolut import RevolutInterestTaxReporter
from polish_pit_calculator.schwab import SchwabEmployeeSponsoredTaxReporter


class TaxReportType(Enum):
    FILES = auto()
    MANUAL = auto()
    PLACEHOLDER = auto()


class TaxReportEnum(Enum):
    SELECT_TAX_REPORT = "— Select Tax Report —"
    SCHWAB_EMPLOYEE_SPONSORED = "Charles Schwab (Employee Sponsored)"
    IB_TRADE_CASH = "Interactive Brokers (Trade Cash)"
    COINBASE_CRYPTO = "Coinbase (Crypto)"
    REVOLUT_INTEREST = "Revolut (Interest)"
    RAW_CUSTOM_CSV = "Raw (Custom CSV)"
    MANUAL = "Manual"

    def to_type(self) -> TaxReportType:
        match self:
            case TaxReportEnum.SELECT_TAX_REPORT:
                return TaxReportType.PLACEHOLDER
            case TaxReportEnum.SCHWAB_EMPLOYEE_SPONSORED:
                return TaxReportType.FILES
            case TaxReportEnum.IB_TRADE_CASH:
                return TaxReportType.FILES
            case TaxReportEnum.COINBASE_CRYPTO:
                return TaxReportType.FILES
            case TaxReportEnum.REVOLUT_INTEREST:
                return TaxReportType.FILES
            case TaxReportEnum.RAW_CUSTOM_CSV:
                return TaxReportType.FILES
            case TaxReportEnum.MANUAL:
                return TaxReportType.MANUAL
            case _ as unknown:
                raise ValueError(f"Unknown TaxReportEnum: {unknown}")

    def to_cls(self) -> Type[TaxReporter]:
        match self:
            case TaxReportEnum.SELECT_TAX_REPORT:
                raise ValueError(
                    "Cannot get TaxReporter class for PLACEHOLDER"
                )
            case TaxReportEnum.SCHWAB_EMPLOYEE_SPONSORED:
                return SchwabEmployeeSponsoredTaxReporter
            case TaxReportEnum.IB_TRADE_CASH:
                return IBTradeCashTaxReporter
            case TaxReportEnum.COINBASE_CRYPTO:
                return CoinbaseTaxReporter
            case TaxReportEnum.REVOLUT_INTEREST:
                return RevolutInterestTaxReporter
            case TaxReportEnum.RAW_CUSTOM_CSV:
                return RawTaxReporter
            case TaxReportEnum.MANUAL:
                return ManualTaxReporter
            case _ as unknown:
                raise ValueError(f"Unknown TaxReportEnum: {unknown}")


@dataclass
class TaxReportEntry:
    tax_report_enum: TaxReportEnum
    tax_report_data: Any


def initialize_state() -> None:
    st.session_state.session_index = st.session_state.get("session_index", 0)
    st.session_state.tax_report_entries = st.session_state.get(
        "tax_report_entries", []
    )
    st.session_state.table = st.session_state.get("table", None)


def setup_and_display_header() -> None:
    title = "Polish PIT Calculator"
    st.set_page_config(page_title=title, layout="centered")
    st.title(title)
    st.markdown(
        """
        <style>
        .block-container {max-width: 1200px; margin: 0 auto;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def resolve_tax_report_enum() -> TaxReportEnum:
    return TaxReportEnum(
        st.selectbox(
            "Tax Report",
            [x.value for x in TaxReportEnum],
            key=f"selected_tax_report_{st.session_state.session_index}",
            disabled=bool(
                st.session_state.get(
                    f"selected_tax_report_files_{st.session_state.session_index}"
                )
                or st.session_state.get(
                    f"selected_tax_report_year_{st.session_state.session_index}",
                    "— Select Year —",
                )
                != "— Select Year —"
            ),
        )
    )


def select_and_submit_files(
    tax_report_enum: TaxReportEnum,
) -> TaxReportEntry | None:
    files = st.file_uploader(
        "Reports (min. 1)",
        key=f"selected_tax_report_files_{st.session_state.session_index}",
        accept_multiple_files=True,
    )
    if files:
        st.markdown("</br>", unsafe_allow_html=True)
        if st.button("Submit"):
            return TaxReportEntry(
                tax_report_enum=tax_report_enum,
                tax_report_data=files,
            )
    return None


def select_and_submit_manual(
    tax_report_enum: TaxReportEnum,
) -> TaxReportEntry | None:
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        current_year = date.today().year
        year = st.selectbox(
            "Tax Year",
            options=list(range(current_year, current_year - 5, -1)),
        )
    if year != "— Select Year —":
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            trade_revenue = st.number_input(
                "Trade Revenue",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c2:
            trade_cost = st.number_input(
                "Trade Cost",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c3:
            trade_loss_from_previous_years = st.number_input(
                "Trade Loss from Previous Years",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            crypto_revenue = st.number_input(
                "Crypto Revenue",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c2:
            crypto_cost = st.number_input(
                "Crypto Cost",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c3:
            crypto_cost_excess_from_previous_years = st.number_input(
                "Crypto Cost Excess from Previous Years",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            domestic_interest = st.number_input(
                "Domestic Interest",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            foreign_interest = st.number_input(
                "Foreign Interest",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c2:
            foreign_interest_withholding_tax = st.number_input(
                "Foreign Interest Withholding Tax",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        with c1:
            employment_revenue = st.number_input(
                "Employment Revenue",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c2:
            employment_cost = st.number_input(
                "Employment Cost",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c3:
            social_security_contributions = st.number_input(
                "Social Security Contributions",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        with c4:
            donations = st.number_input(
                "Donations",
                value=0.0,
                min_value=0.0,
                format="%.2f",
            )
        st.markdown("</br>", unsafe_allow_html=True)
        if st.button("Submit"):
            tax_data = {
                "trade_revenue": trade_revenue,
                "trade_cost": trade_cost,
                "trade_loss_from_previous_years": trade_loss_from_previous_years,
                "crypto_revenue": crypto_revenue,
                "crypto_cost": crypto_cost,
                "crypto_cost_excess_from_previous_years": crypto_cost_excess_from_previous_years,
                "domestic_interest": domestic_interest,
                "foreign_interest": foreign_interest,
                "foreign_interest_withholding_tax": foreign_interest_withholding_tax,
                "employment_revenue": employment_revenue,
                "employment_cost": employment_cost,
                "social_security_contributions": social_security_contributions,
                "donations": donations,
            }
            return TaxReportEntry(
                tax_report_enum=tax_report_enum,
                tax_report_data={
                    "year": int(year),
                    "tax_data": tax_data,
                },
            )
    return None


def display_tax_report_entries() -> None:
    for i, tax_report_entry in enumerate(st.session_state.tax_report_entries):
        tax_report_entry = cast(TaxReportEntry, tax_report_entry)
        dg1, dg2, dg3 = st.columns([0.4, 0.5, 0.1])
        with dg1:
            st.markdown(
                f"**#{i + 1}** — {tax_report_entry.tax_report_enum.value}"
            )
        with dg2:
            match tax_report_entry.tax_report_enum.to_type().value:
                case TaxReportType.FILES.value:
                    series = pd.Series(
                        [f.name for f in tax_report_entry.tax_report_data],
                        name="Uploaded Files",
                    )
                    st.dataframe(series, hide_index=True)
                case TaxReportType.MANUAL.value:
                    tax_data = tax_report_entry.tax_report_data["tax_data"]
                    series = pd.Series(
                        tax_data,
                        name=str(tax_report_entry.tax_report_data["year"]),
                    ).apply(lambda x: f"{x:,.2f}")
                    st.dataframe(series)
                case _ as unknown:
                    raise ValueError(f"Unknown TaxReportType value: {unknown}")
        with dg3:
            if st.button("Delete", key=i):
                st.session_state.tax_report_entries.pop(i)
                summarize_tax_reports()
                st.rerun()


def summarize_tax_reports() -> None:
    if not st.session_state.tax_report_entries:
        st.session_state.table = None
        return
    tax_report = TaxReport()
    for tax_report_entry in st.session_state.tax_report_entries:
        tax_report_entry = cast(TaxReportEntry, tax_report_entry)
        tax_reporter_cls = tax_report_entry.tax_report_enum.to_cls()
        match tax_report_entry.tax_report_enum.to_type().value:
            case TaxReportType.FILES.value:
                for f in tax_report_entry.tax_report_data:
                    f.seek(0)
                tax_reporter = tax_reporter_cls(
                    *tax_report_entry.tax_report_data
                )
            case TaxReportType.MANUAL.value:
                tax_reporter = tax_reporter_cls(
                    tax_report_entry.tax_report_data
                )
            case _ as unknown:
                raise ValueError(f"Unknown TaxReportType value: {unknown}")
        tax_report += tax_reporter.generate()
    df = tax_report.to_dataframe()
    st.session_state.table = df


def main() -> None:
    initialize_state()
    setup_and_display_header()
    tax_report_enum = resolve_tax_report_enum()
    match tax_report_enum.to_type():
        case TaxReportType.PLACEHOLDER:
            tax_report_entry = None
        case TaxReportType.FILES:
            tax_report_entry = select_and_submit_files(tax_report_enum)
        case TaxReportType.MANUAL:
            tax_report_entry = select_and_submit_manual(tax_report_enum)
        case _ as unknown:
            raise ValueError(f"Unknown TaxReportType: {unknown}")
    if tax_report_entry is not None:
        st.session_state.tax_report_entries.append(tax_report_entry)
        st.session_state.session_index += 1
        summarize_tax_reports()
        st.rerun()
    if st.session_state.tax_report_entries:
        st.markdown("</br>", unsafe_allow_html=True)
        st.markdown("### Submitted Tax Reports:</br>", unsafe_allow_html=True)
        display_tax_report_entries()
        st.markdown("</br>", unsafe_allow_html=True)
    if st.session_state.table is not None:
        st.markdown("</br>", unsafe_allow_html=True)
        st.dataframe(st.session_state.table)


if __name__ == "__main__":
    main()
