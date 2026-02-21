"""Charles Schwab employee-sponsored JSON reporter implementation."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import cast

import pandas as pd

from src.config import JsonTaxReporter, TaxRecord, TaxReport
from src.utils import get_exchange_rate

_SHARE_FIELDS = ("Shares", "NetSharesDeposited", "SharesWithheld", "SharesSold")
_PRICE_FIELDS = (
    "SalePrice",
    "PurchasePrice",
    "SubscriptionFairMarketValue",
    "VestFairMarketValue",
    "FairMarketValuePrice",
    "PurchaseFairMarketValue",
)
_REFERENCE_FIELD_SPECS = (
    ("VestDate", "VestFairMarketValue"),
    ("PurchaseDate", "PurchasePrice"),
    ("SubscriptionDate", "SubscriptionFairMarketValue"),
)
_ValueMap = dict[str, float]
ReferenceContext = tuple[_ValueMap, _ValueMap, _ValueMap, float | None, float | None]


@dataclass(frozen=True)
class _SplitParams:
    split_date: date
    factor: int
    is_reverse: bool


@dataclass(frozen=True)
class _AlignPolicy:
    actions: frozenset[str]
    default_scale_when_unknown: bool
    enrich_references: bool


@dataclass(frozen=True)
class _ScaleContext:
    split: _SplitParams
    references: ReferenceContext
    default_scale_when_unknown: bool


@dataclass(frozen=True)
class _SaleDetectionConfig:
    window: int
    minimum_ratio: float
    sustain_ratio: float
    is_reverse: bool


@dataclass
class _ReferenceCollectors:
    vest_values: dict[str, list[float]]
    purchase_values: dict[str, list[float]]
    subscription_values: dict[str, list[float]]
    post_sale_prices: list[float]


class SchwabEmployeeSponsoredTaxReporter(JsonTaxReporter):
    """Build tax report from Schwab employee-sponsored account exports."""

    def generate(self) -> TaxReport:
        df = self._load_report()
        remaining: dict[str, list[pd.Series]] = defaultdict(list)
        tax_report = TaxReport()
        for _, row in df.iterrows():
            year = row["Date"].year
            if row["Action"] == "Deposit":
                for _ in range(int(row["Quantity"])):
                    remaining[row["Description"]].append(row)
            elif row["Action"] == "Sale":
                exc_rate = get_exchange_rate(row["Currency"], row["Date"])
                tax_record = TaxRecord(trade_cost=row["FeesAndCommissions"] * exc_rate)
                for _ in range(int(row["Shares"])):
                    sold_row = remaining[row["Type"]].pop(0)
                    sold_exc_rate = get_exchange_rate(sold_row["Currency"], sold_row["Date"])
                    tax_record += TaxRecord(
                        trade_revenue=row["SalePrice"] * exc_rate,
                        trade_cost=sold_row["PurchasePrice"] * sold_exc_rate,
                    )
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Lapse":
                pass
            elif row["Action"] == "Dividend":
                exc_rate = get_exchange_rate(row["Currency"], row["Date"])
                tax_record = TaxRecord(foreign_interest=row["Amount"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Tax Withholding":
                exc_rate = get_exchange_rate(row["Currency"], row["Date"])
                tax_record = TaxRecord(foreign_interest_withholding_tax=-row["Amount"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            elif row["Action"] == "Wire Transfer":
                exc_rate = get_exchange_rate(row["Currency"], row["Date"])
                tax_record = TaxRecord(trade_cost=-row["FeesAndCommissions"] * exc_rate)
                tax_report += TaxReport({year: tax_record})
            else:
                raise ValueError(f"Unknown action: {row['Action']}")
        return tax_report

    def _parse_amount_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if "Currency" not in df.columns:
            df["Currency"] = pd.NA
        for col in [
            "Amount",
            "SalePrice",
            "PurchasePrice",
            "FeesAndCommissions",
            "FairMarketValuePrice",
            "VestFairMarketValue",
        ]:
            if col not in df.columns:
                df[col] = ""
            parsed = (
                df[col]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.extract(r"(-?)([$\u20AC£]?)([\d,\.]+)")
            )
            sign = parsed[0].apply(lambda x: -1 if x == "-" else 1)
            currency = parsed[1].replace({"$": "USD", "€": "EUR", "£": "GBP", "": pd.NA})
            amount = (
                parsed[2]
                .apply(lambda x: x.replace(",", "") if isinstance(x, str) else 0)
                .astype(float)
            )
            df[col] = sign * amount
            df["Currency"] = df["Currency"].combine_first(currency)
        return df

    def _flatten_transaction(self, transaction: dict[str, object]) -> list[dict[str, object]]:
        details_raw = transaction.get("TransactionDetails")
        details = []
        if isinstance(details_raw, list):
            for item in details_raw:
                if isinstance(item, dict) and isinstance(item.get("Details"), dict):
                    details.append(item["Details"])
        if not details:
            details = [{}]

        rows: list[dict[str, object]] = []
        for index, detail in enumerate(details):
            row: dict[str, object] = {
                "Date": transaction.get("Date"),
                "Action": transaction.get("Action"),
                "Description": transaction.get("Description"),
                "Quantity": transaction.get("Quantity"),
                "Amount": transaction.get("Amount"),
                "FeesAndCommissions": transaction.get("FeesAndCommissions"),
                "Type": detail.get("Type") if isinstance(detail, dict) else None,
                "Shares": detail.get("Shares") if isinstance(detail, dict) else None,
                "SalePrice": detail.get("SalePrice") if isinstance(detail, dict) else None,
                "PurchasePrice": detail.get("PurchasePrice") if isinstance(detail, dict) else None,
                "FairMarketValuePrice": (
                    detail.get("FairMarketValuePrice") if isinstance(detail, dict) else None
                ),
                "VestFairMarketValue": (
                    detail.get("VestFairMarketValue") if isinstance(detail, dict) else None
                ),
            }

            if row["Type"] in {None, ""}:
                row["Type"] = transaction.get("Description")
            if row["Action"] == "Sale" and index > 0:
                row["FeesAndCommissions"] = "$0.00"
            rows.append(row)
        return rows

    def _load_report(self) -> pd.DataFrame:
        self.alignment_change_log = []
        rows: list[dict[str, object]] = []
        for json_file in self.files:
            with Path(json_file).open("r", encoding="utf-8") as path_file:
                payload = json.load(path_file)
            if not isinstance(payload, dict):
                continue
            transactions_raw = payload.get("Transactions")
            if not isinstance(transactions_raw, list):
                continue
            payload = self._align_and_validate_payload(payload)
            aligned_transactions = payload.get("Transactions")
            if not isinstance(aligned_transactions, list):
                continue
            for transaction in aligned_transactions:
                if not isinstance(transaction, dict):
                    continue
                rows.extend(self._flatten_transaction(transaction))
        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y").dt.date
        df["Type"] = df["Type"].fillna(df["Description"])
        df["Shares"] = pd.to_numeric(df["Shares"], errors="coerce").fillna(0).astype(int)
        df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
        df = self._parse_amount_columns(df)
        return df.sort_values("Date", kind="mergesort").reset_index(drop=True)

    def _align_and_validate_payload(self, payload: dict[str, object]) -> dict[str, object]:
        aligned = json.loads(json.dumps(payload))
        transactions = aligned.get("Transactions")
        if not isinstance(transactions, list):
            return aligned
        detected = self._detect_split_params(transactions)
        if detected is None:
            return aligned
        split = _SplitParams(*detected)
        reference_context = self._build_reference_context(transactions, split.split_date)
        sale_policy = _AlignPolicy(frozenset({"Sale"}), True, True)
        lot_policy = _AlignPolicy(frozenset({"Deposit", "Lapse"}), False, False)
        self._align_transactions_for_actions(transactions, split, reference_context, sale_policy)
        self._align_transactions_for_actions(transactions, split, reference_context, lot_policy)
        self._raise_alignment_validation_errors(transactions)
        return aligned

    def _align_transactions_for_actions(
        self,
        transactions: list[object],
        split: _SplitParams,
        reference_context: ReferenceContext,
        policy: _AlignPolicy,
    ) -> None:
        scale_context = _ScaleContext(split, reference_context, policy.default_scale_when_unknown)
        for tx in transactions:
            scaled = self._align_transaction_before_split(tx, scale_context, policy.actions)
            if scaled and policy.enrich_references and isinstance(tx, dict):
                self._update_reference_context_from_transaction(tx, reference_context)

    def _align_transaction_before_split(
        self,
        transaction: object,
        scale_context: _ScaleContext,
        actions: frozenset[str],
    ) -> bool:
        if not isinstance(transaction, dict):
            return False
        action = transaction.get("Action")
        if not isinstance(action, str) or action not in actions:
            return False
        tx_date = self._parse_tx_date(transaction.get("Date"))
        if tx_date is None or tx_date >= scale_context.split.split_date:
            return False
        detail_rows_obj = transaction.get("TransactionDetails")
        if not isinstance(detail_rows_obj, list):
            return False
        detail_dicts = list(self._iter_detail_dicts(detail_rows_obj))
        tx_instrument = (
            cast(str, transaction.get("Description"))
            if isinstance(transaction.get("Description"), str)
            else "UNKNOWN"
        )
        tx_label, quantity_before = transaction.get("Date"), transaction.get("Quantity")
        log_start = len(self.alignment_change_log)
        if not self._scale_detail_rows(
            detail_dicts,
            action,
            scale_context,
            (tx_label, tx_instrument),
        ):
            return False
        split_factor, split_reverse = scale_context.split.factor, scale_context.split.is_reverse
        self._update_scaled_transaction_quantity(
            transaction,
            detail_rows_obj,
            split_factor,
            split_reverse,
        )
        if (
            quantity_before != transaction.get("Quantity")
            and len(self.alignment_change_log) > log_start
        ):
            quantity_change = (
                f"Quantity: \x1b[31m{quantity_before}\x1b[0m -> "
                f"\x1b[32m{transaction.get('Quantity')}\x1b[0m"
            )
            self.alignment_change_log[-1] = f"{self.alignment_change_log[-1]}; {quantity_change}"
        return True

    def _scale_detail_rows(
        self,
        detail_rows: list[dict[str, object]],
        action: str,
        scale_context: _ScaleContext,
        tx_identity: tuple[object, str],
    ) -> bool:
        scaled_any = False
        for detail in detail_rows:
            if not self._should_scale_detail(detail, action, scale_context):
                continue
            before = detail.copy()
            self._scale_detail(detail, scale_context.split.factor, scale_context.split.is_reverse)
            changes: list[str] = []
            for key in (*_SHARE_FIELDS, *_PRICE_FIELDS):
                if before.get(key) == detail.get(key):
                    continue
                changes.append(
                    f"{key}: \x1b[31m{before.get(key)}\x1b[0m -> \x1b[32m{detail.get(key)}\x1b[0m"
                )
            if changes:
                detail_type = detail.get("Type")
                detail_value = (
                    detail_type if isinstance(detail_type, str) and detail_type else tx_identity[1]
                )
                self.alignment_change_log.append(
                    f"{tx_identity[0]} {action} {detail_value}; " + "; ".join(changes)
                )
            scaled_any = True
        return scaled_any

    def _iter_detail_dicts(self, detail_rows: list[object]):
        for detail_row in detail_rows:
            if not isinstance(detail_row, dict):
                continue
            detail = detail_row.get("Details")
            if isinstance(detail, dict):
                yield detail

    def _update_scaled_transaction_quantity(
        self,
        transaction: dict[str, object],
        detail_rows: list[object],
        split_factor: int,
        is_reverse: bool,
    ) -> None:
        action = transaction.get("Action")
        if action == "Sale":
            transaction["Quantity"] = self._sum_sale_shares(
                detail_rows,
                transaction.get("Quantity"),
            )
        elif action in {"Deposit", "Lapse"}:
            transaction["Quantity"] = self._scale_quantity_value(
                transaction.get("Quantity"),
                split_factor,
                is_reverse,
            )

    def _raise_alignment_validation_errors(
        self,
        transactions: list[object],
    ) -> None:
        sale_errors = self._validate_sale_amounts(transactions)
        if sale_errors:
            raise ValueError(f"Schwab split alignment validation failed: {sale_errors[0]}")
        basis_errors = self._validate_cost_basis(transactions)
        if basis_errors:
            raise ValueError(f"Schwab split alignment validation failed: {basis_errors[0]}")

    def _detect_split_params(
        self,
        transactions: list[object],
    ) -> tuple[date, int, bool] | None:
        groups = self._collect_scale_groups(transactions)
        group_candidates: list[tuple[date, int, bool, int]] = []
        for observations in groups.values():
            candidate = self._candidate_from_group(observations)
            if candidate is not None:
                split_date, factor, is_reverse = candidate
                group_candidates.append((split_date, factor, is_reverse, len(observations)))
        if not group_candidates:
            return self._detect_split_params_from_unit_values(transactions)

        factor_direction_weights: Counter[tuple[int, bool]] = Counter()
        for _split_date, factor, is_reverse, weight in group_candidates:
            factor_direction_weights[(factor, is_reverse)] += weight
        factor, is_reverse = max(factor_direction_weights.items(), key=lambda item: item[1])[0]
        candidate_dates = sorted(
            split_date
            for split_date, cand_factor, cand_reverse, _weight in group_candidates
            if cand_factor == factor and cand_reverse == is_reverse
        )
        split_date = candidate_dates[len(candidate_dates) // 2]
        sales_split_date = self._detect_split_date_from_sales(transactions, factor, is_reverse)
        if sales_split_date is not None:
            split_date = sales_split_date
        return split_date, factor, is_reverse

    def _detect_split_params_from_unit_values(
        self,
        transactions: list[object],
    ) -> tuple[date, int, bool] | None:
        observations = self._collect_unkeyed_unit_value_observations(transactions)
        if not observations:
            return None
        return self._candidate_from_group(observations)

    def _collect_unkeyed_unit_value_observations(
        self,
        transactions: list[object],
    ) -> list[tuple[date, float]]:
        observations: list[tuple[date, float]] = []
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            tx_date = self._parse_tx_date(tx.get("Date"))
            if tx_date is None:
                continue
            detail_rows_obj = tx.get("TransactionDetails")
            if not isinstance(detail_rows_obj, list):
                continue
            for detail in self._iter_detail_dicts(detail_rows_obj):
                value = self._first_positive_unit_value(detail)
                if value is None:
                    continue
                observations.append((tx_date, value))
        return observations

    def _first_positive_unit_value(self, detail: dict[str, object]) -> float | None:
        for value_key in (
            "VestFairMarketValue",
            "PurchasePrice",
            "SubscriptionFairMarketValue",
        ):
            value, _ = self._parse_money(detail.get(value_key))
            if value is not None and value > 0:
                return value
        return None

    def _collect_scale_groups(
        self,
        transactions: list[object],
    ) -> dict[tuple[str, str], list[tuple[date, float]]]:
        groups: dict[tuple[str, str], list[tuple[date, float]]] = defaultdict(list)
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            tx_date = self._parse_tx_date(tx.get("Date"))
            if tx_date is None:
                continue
            detail_rows_obj = tx.get("TransactionDetails")
            if not isinstance(detail_rows_obj, list):
                continue
            for detail in self._iter_detail_dicts(detail_rows_obj):
                vest_date = detail.get("VestDate")
                vest_value, _ = self._parse_money(detail.get("VestFairMarketValue"))
                if isinstance(vest_date, str) and vest_value is not None and vest_value > 0:
                    groups[("vest", vest_date)].append((tx_date, vest_value))

                purchase_date = detail.get("PurchaseDate")
                purchase_value, _ = self._parse_money(detail.get("PurchasePrice"))
                if (
                    isinstance(purchase_date, str)
                    and purchase_value is not None
                    and purchase_value > 0
                ):
                    groups[("purchase", purchase_date)].append((tx_date, purchase_value))

                subscription_date = detail.get("SubscriptionDate")
                subscription_value, _ = self._parse_money(detail.get("SubscriptionFairMarketValue"))
                if (
                    isinstance(subscription_date, str)
                    and subscription_value is not None
                    and subscription_value > 0
                ):
                    groups[("subscription", subscription_date)].append(
                        (tx_date, subscription_value)
                    )
        return groups

    def _candidate_from_group(
        self,
        observations: list[tuple[date, float]],
    ) -> tuple[date, int, bool] | None:
        if len(observations) < 2:
            return None
        observations = sorted(observations, key=lambda item: item[0])
        values = [value for _, value in observations if value > 0]
        if len(values) < 2:
            return None

        low = min(values)
        high = max(values)
        factor = self._factor_from_ratio(high / low)
        if factor is None:
            return None

        pivot_size = max(1, min(3, len(observations) // 3))
        first_value = median(value for _, value in observations[:pivot_size])
        last_value = median(value for _, value in observations[-pivot_size:])
        is_reverse = first_value < last_value
        midpoint = (high * low) ** 0.5
        split_date = min(observations, key=lambda item: abs(item[1] - midpoint))[0]
        return split_date, factor, is_reverse

    def _detect_split_date_from_sales(
        self,
        transactions: list[object],
        factor: int,
        is_reverse: bool,
    ) -> date | None:
        series = self._sale_price_series(transactions)
        if len(series) < 6:
            return None
        config = _SaleDetectionConfig(
            window=3 if len(series) <= 30 else 4,
            minimum_ratio=max(1.8, factor * 0.55),
            sustain_ratio=max(1.6, factor * 0.40),
            is_reverse=is_reverse,
        )
        candidates: list[date] = []
        for idx in range(config.window, len(series) - config.window + 1):
            candidate = self._sale_split_candidate_at_index(
                series,
                idx,
                config,
            )
            if candidate is not None:
                candidates.append(candidate)
        return min(candidates) if candidates else None

    def _sale_split_candidate_at_index(
        self,
        series: list[tuple[date, float]],
        index: int,
        config: _SaleDetectionConfig,
    ) -> date | None:
        pre_values = [value for _, value in series[index - config.window : index]]
        post_window = series[index : index + config.window]
        post_values = [value for _, value in post_window]
        pre_median = median(pre_values)
        post_median = median(post_values)
        ratio = post_median / pre_median if config.is_reverse else pre_median / post_median
        if ratio < config.minimum_ratio:
            return None
        if not self._satisfies_sustain_ratio(series, index, pre_median, config):
            return None
        return self._infer_transition_date_from_window(
            post_window,
            pre_median,
            post_median,
            config.is_reverse,
        )

    def _satisfies_sustain_ratio(
        self,
        series: list[tuple[date, float]],
        index: int,
        pre_median: float,
        config: _SaleDetectionConfig,
    ) -> bool:
        later_window = series[index + config.window : index + (2 * config.window)]
        if not later_window:
            return True
        later_median = median(value for _, value in later_window)
        ratio = later_median / pre_median if config.is_reverse else pre_median / later_median
        return ratio >= config.sustain_ratio

    def _infer_transition_date_from_window(
        self,
        post_window: list[tuple[date, float]],
        pre_median: float,
        post_median: float,
        is_reverse: bool,
    ) -> date:
        midpoint = (pre_median * post_median) ** 0.5
        for obs_date, value in post_window:
            in_new_regime = value > midpoint if is_reverse else value < midpoint
            if in_new_regime:
                return obs_date
        return post_window[0][0]

    def _sale_price_series(self, transactions: list[object]) -> list[tuple[date, float]]:
        sale_prices_by_date: dict[date, list[float]] = defaultdict(list)
        for tx in transactions:
            if not isinstance(tx, dict) or tx.get("Action") != "Sale":
                continue
            tx_date = self._parse_tx_date(tx.get("Date"))
            if tx_date is None:
                continue
            detail_rows_obj = tx.get("TransactionDetails")
            if not isinstance(detail_rows_obj, list):
                continue
            for detail in self._iter_detail_dicts(detail_rows_obj):
                sale_price, _ = self._parse_money(detail.get("SalePrice"))
                if sale_price is None or sale_price <= 0:
                    continue
                sale_prices_by_date[tx_date].append(sale_price)
        return sorted(
            (obs_date, median(values)) for obs_date, values in sale_prices_by_date.items() if values
        )

    def _build_reference_context(
        self,
        transactions: list[object],
        split_date: date,
    ) -> ReferenceContext:
        collectors = _ReferenceCollectors(
            vest_values=defaultdict(list),
            purchase_values=defaultdict(list),
            subscription_values=defaultdict(list),
            post_sale_prices=[],
        )

        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            tx_date = self._parse_tx_date(tx.get("Date"))
            if tx_date is None or tx_date < split_date:
                continue
            detail_rows_obj = tx.get("TransactionDetails")
            if not isinstance(detail_rows_obj, list):
                continue
            for detail in self._iter_detail_dicts(detail_rows_obj):
                self._append_reference_values(detail, collectors)

        vest_map = {key: median(values) for key, values in collectors.vest_values.items() if values}
        purchase_map = {
            key: median(values) for key, values in collectors.purchase_values.items() if values
        }
        subscription_map = {
            key: median(values) for key, values in collectors.subscription_values.items() if values
        }
        post_sale_max = max(collectors.post_sale_prices) if collectors.post_sale_prices else None
        post_sale_min = min(collectors.post_sale_prices) if collectors.post_sale_prices else None
        return vest_map, purchase_map, subscription_map, post_sale_min, post_sale_max

    def _append_reference_values(
        self,
        detail: dict[str, object],
        collectors: _ReferenceCollectors,
    ) -> None:
        vest_date = detail.get("VestDate")
        vest_value, _ = self._parse_money(detail.get("VestFairMarketValue"))
        if isinstance(vest_date, str) and vest_value is not None:
            collectors.vest_values[vest_date].append(vest_value)

        purchase_date = detail.get("PurchaseDate")
        purchase_value, _ = self._parse_money(detail.get("PurchasePrice"))
        if isinstance(purchase_date, str) and purchase_value is not None:
            collectors.purchase_values[purchase_date].append(purchase_value)

        subscription_date = detail.get("SubscriptionDate")
        subscription_value, _ = self._parse_money(detail.get("SubscriptionFairMarketValue"))
        if isinstance(subscription_date, str) and subscription_value is not None:
            collectors.subscription_values[subscription_date].append(subscription_value)

        sale_price, _ = self._parse_money(detail.get("SalePrice"))
        if sale_price is not None:
            collectors.post_sale_prices.append(sale_price)

    def _update_reference_context_from_transaction(
        self,
        transaction: dict[str, object],
        reference_context: ReferenceContext,
    ) -> None:
        detail_rows_obj = transaction.get("TransactionDetails")
        if not isinstance(detail_rows_obj, list):
            return
        vest_map, purchase_map, subscription_map, _min_price, _max_price = reference_context
        for detail in self._iter_detail_dicts(detail_rows_obj):
            self._add_reference_value(vest_map, detail, "VestDate", "VestFairMarketValue")
            self._add_reference_value(purchase_map, detail, "PurchaseDate", "PurchasePrice")
            self._add_reference_value(
                subscription_map,
                detail,
                "SubscriptionDate",
                "SubscriptionFairMarketValue",
            )

    def _add_reference_value(
        self,
        reference_map: dict[str, float],
        detail: dict[str, object],
        date_key: str,
        value_key: str,
    ) -> None:
        key = detail.get(date_key)
        if not isinstance(key, str) or key in reference_map:
            return
        value, _ = self._parse_money(detail.get(value_key))
        if value is None or value <= 0:
            return
        reference_map[key] = value

    def _factor_from_ratio(self, ratio: float) -> int | None:
        factor = int(round(ratio))
        if factor < 2:
            return None
        if ratio < 1.8:
            return None
        if abs(ratio - factor) > 0.35:
            return None
        return factor

    def _parse_tx_date(self, value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%m/%d/%Y").date()
        except ValueError:
            return None

    def _parse_number(self, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, int | float):
            return float(value)
        if not isinstance(value, str):
            return None
        stripped = value.strip().replace(",", "")
        if stripped == "":
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    def _parse_money(self, value: object) -> tuple[float | None, str]:
        if value is None:
            return None, ""
        if isinstance(value, int | float):
            return float(value), ""
        if not isinstance(value, str):
            return None, ""
        stripped = value.strip()
        if stripped == "":
            return None, ""
        sign = -1.0 if stripped.startswith("-") else 1.0
        if stripped.startswith("-"):
            stripped = stripped[1:].strip()
        symbol = stripped[0] if stripped and stripped[0] in {"$", "€", "£"} else ""
        if symbol:
            stripped = stripped[1:]
        try:
            return sign * float(stripped.replace(",", "")), symbol
        except ValueError:
            return None, symbol

    def _format_number_like(self, original: object, value: float) -> object:
        if isinstance(original, int):
            return int(round(value))
        if isinstance(original, float):
            return value
        if not isinstance(original, str):
            return original
        rounded = round(value)
        if abs(value - rounded) < 1e-9:
            return str(int(rounded))
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _format_money_like(self, original: object, value: float, symbol: str) -> object:
        if isinstance(original, int):
            return int(round(value))
        if isinstance(original, float):
            return value
        if not isinstance(original, str):
            return original
        sign = "-" if value < 0 else ""
        amount = f"{abs(value):,.6f}".rstrip("0").rstrip(".")
        return f"{sign}{symbol}{amount}"

    def _should_scale_detail(
        self,
        detail: dict[str, object],
        action: object,
        scale_context: _ScaleContext,
    ) -> bool:
        if action not in {"Sale", "Deposit", "Lapse"}:
            return False
        scores = self._detail_scale_scores(detail, scale_context)
        if scores is None:
            return scale_context.default_scale_when_unknown
        scale_score, keep_score = scores
        if scale_score == keep_score:
            return scale_context.default_scale_when_unknown
        return scale_score > keep_score

    def _detail_scale_scores(
        self,
        detail: dict[str, object],
        scale_context: _ScaleContext,
    ) -> tuple[int, int] | None:
        split = scale_context.split
        post_sale_min, post_sale_max = scale_context.references[3], scale_context.references[4]
        scale_score, keep_score, matched_reference = self._score_reference_fields(
            detail,
            scale_context,
        )

        sale_price, _ = self._parse_money(detail.get("SalePrice"))
        if (
            sale_price is not None
            and post_sale_min is not None
            and post_sale_max is not None
            and self._price_range_suggests_scaling(sale_price, post_sale_min, post_sale_max, split)
        ):
            matched_reference = True
            scale_score += 1
        if not matched_reference:
            return None
        return scale_score, keep_score

    def _score_reference_fields(
        self,
        detail: dict[str, object],
        scale_context: _ScaleContext,
    ) -> tuple[int, int, bool]:
        split = scale_context.split
        reference_maps = scale_context.references[:3]
        scale_score = 0
        keep_score = 0
        matched_reference = False
        for spec, reference_map in zip(_REFERENCE_FIELD_SPECS, reference_maps, strict=True):
            score = self._score_single_reference(
                detail,
                spec,
                reference_map,
                split,
            )
            scale_score += score[0]
            keep_score += score[1]
            matched_reference = matched_reference or score[2]
        return scale_score, keep_score, matched_reference

    def _score_single_reference(
        self,
        detail: dict[str, object],
        spec: tuple[str, str],
        reference_map: dict[str, float],
        split: _SplitParams,
    ) -> tuple[int, int, bool]:
        date_key, value_key = spec
        key = detail.get(date_key)
        if not isinstance(key, str):
            return 0, 0, False
        value, _ = self._parse_money(detail.get(value_key))
        if value is None:
            return 0, 0, False
        reference_value = reference_map.get(key)
        if reference_value is None:
            return 0, 0, False
        if self._closer_to_scaled_value(value, reference_value, split.factor, split.is_reverse):
            return 3, 0, True
        if self._is_close(value, reference_value):
            return 0, 3, True
        return 0, 0, True

    def _closer_to_scaled_value(
        self,
        value: float,
        canonical_value: float,
        split_factor: int,
        is_reverse: bool,
    ) -> bool:
        scaled_reference = (
            canonical_value / split_factor if is_reverse else canonical_value * split_factor
        )
        keep_distance = abs(value - canonical_value)
        scale_distance = abs(value - scaled_reference)
        return scale_distance + max(0.01, abs(canonical_value) * 0.03) < keep_distance

    def _is_close(self, value: float, reference: float) -> bool:
        tolerance = max(0.01, abs(reference) * 0.03)
        return abs(value - reference) <= tolerance

    def _price_range_suggests_scaling(
        self,
        price: float,
        post_sale_min: float,
        post_sale_max: float,
        split: _SplitParams,
    ) -> bool:
        if split.is_reverse:
            return price < (post_sale_min / 1.6) and (price * split.factor) >= (post_sale_min / 1.6)
        return price > (post_sale_max * 1.6) and (price / split.factor) <= (post_sale_max * 1.6)

    def _scale_detail(
        self,
        detail: dict[str, object],
        factor: int,
        is_reverse: bool,
    ) -> None:
        share_multiplier = 1 / factor if is_reverse else factor
        price_multiplier = factor if is_reverse else 1 / factor
        for key in _SHARE_FIELDS:
            parsed = self._parse_number(detail.get(key))
            if parsed is None:
                continue
            detail[key] = self._format_number_like(detail.get(key), parsed * share_multiplier)
        for key in _PRICE_FIELDS:
            parsed, symbol = self._parse_money(detail.get(key))
            if parsed is None:
                continue
            detail[key] = self._format_money_like(
                detail.get(key),
                parsed * price_multiplier,
                symbol,
            )

    def _sum_sale_shares(
        self,
        detail_rows: list[object],
        original_quantity: object,
    ) -> object:
        total = 0.0
        for detail_row in detail_rows:
            if not isinstance(detail_row, dict):
                continue
            detail = detail_row.get("Details")
            if not isinstance(detail, dict):
                continue
            shares = self._parse_number(detail.get("Shares"))
            if shares is not None:
                total += shares
        return self._format_number_like(original_quantity, total)

    def _scale_quantity_value(
        self,
        quantity: object,
        factor: int,
        is_reverse: bool,
    ) -> object:
        parsed = self._parse_number(quantity)
        if parsed is None:
            return quantity
        multiplier = 1 / factor if is_reverse else factor
        return self._format_number_like(quantity, parsed * multiplier)

    def _validate_sale_amounts(self, transactions: list[object]) -> list[str]:
        errors: list[str] = []
        for tx in transactions:
            if not isinstance(tx, dict) or tx.get("Action") != "Sale":
                continue
            amount, _ = self._parse_money(tx.get("Amount"))
            if amount is None:
                continue
            fees, _ = self._parse_money(tx.get("FeesAndCommissions"))
            fees = fees or 0.0
            subtotal = 0.0
            has_lot = False
            detail_rows = tx.get("TransactionDetails")
            if not isinstance(detail_rows, list):
                continue
            for detail_row in detail_rows:
                if not isinstance(detail_row, dict):
                    continue
                detail = detail_row.get("Details")
                if not isinstance(detail, dict):
                    continue
                shares = self._parse_number(detail.get("Shares"))
                sale_price, _ = self._parse_money(detail.get("SalePrice"))
                if shares is None or sale_price is None:
                    continue
                subtotal += shares * sale_price
                has_lot = True
            if has_lot and abs((subtotal - fees) - amount) > 0.05:
                errors.append(f"{tx.get('Date')} sale amount mismatch")
        return errors

    def _validate_cost_basis(self, transactions: list[object]) -> list[str]:
        errors: list[str] = []
        for tx in transactions:
            if not isinstance(tx, dict) or tx.get("Action") != "Sale":
                continue
            detail_rows = tx.get("TransactionDetails")
            if not isinstance(detail_rows, list):
                continue
            for detail_row in detail_rows:
                if not isinstance(detail_row, dict):
                    continue
                detail = detail_row.get("Details")
                if not isinstance(detail, dict):
                    continue
                shares = self._parse_number(detail.get("Shares"))
                cost_basis, _ = self._parse_money(detail.get("TotalCostBasis"))
                vest_price, _ = self._parse_money(detail.get("VestFairMarketValue"))
                purchase_price, _ = self._parse_money(detail.get("PurchasePrice"))
                if shares is None or cost_basis is None:
                    continue
                unit_price = vest_price if vest_price is not None else purchase_price
                if unit_price is None:
                    continue
                if abs((shares * unit_price) - cost_basis) > 0.1:
                    errors.append(f"{tx.get('Date')} cost basis mismatch")
        return errors
