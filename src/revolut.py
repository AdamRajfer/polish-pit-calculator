import pandas as pd

from src.config import TaxRecord, TaxReport, TaxReporter


class RevolutInterestTaxReporter(TaxReporter):
    def generate(self) -> TaxReport:
        df = self._load_report()
        tax_report = TaxReport()
        for year, df_year in df.groupby("Year"):
            tax_report[year] = TaxRecord(
                domestic_interest=df_year["Money in"].sum()
            )
        return tax_report

    def _load_report(self) -> pd.DataFrame:
        reports = []
        for arg in self.args:
            report = pd.read_csv(arg)
            reports.append(report)
        df = pd.concat(reports, ignore_index=True)
        df = df[df["Description"].str.startswith("Gross interest")]
        df["Completed Date"] = pd.to_datetime(
            df["Completed Date"], dayfirst=True
        )
        df = df.sort_values(by="Completed Date", ignore_index=True)
        df["Year"] = df["Completed Date"].dt.year
        df["Money in"] = (
            df["Money in"]
            .str.replace(",", "", regex=False)
            .str.extract(r"([+-]?\d+(?:\.\d*)?)")
            .astype(float)
        )
        return df
