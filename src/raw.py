import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter


class RawTaxReporter(TaxReporter):
    def generate(self) -> TaxReport:
        df = self._load_report()
        tax_report = TaxReport()
        for year, tax_record_data in (
            df.drop(columns="description").groupby("year").sum().iterrows()
        ):
            tax_report[year] = TaxRecord(**tax_record_data)
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        reports = []
        for arg in self.args:
            report = pd.read_csv(arg)
            reports.append(report)
        return pd.concat(reports, ignore_index=True).fillna(0.0)
