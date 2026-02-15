from datetime import date, datetime, timedelta
from functools import partial

import numpy as np
from scipy.optimize import minimize


class SavingsForTaxOptimizer:
    def __init__(
        self,
        tax_rate: float = 0.19,
        payment_month: int = 4,
        payment_day: int = 30,
        delay: int = 14,
        tolerance: float = 1e-2,
    ) -> None:
        self.tax_rate = tax_rate
        self.payment_month = payment_month
        self.payment_day = payment_day
        self.delay = delay
        self.tolerance = tolerance
        self.required_cash_: float | None = None
        self.msg_: str | None = None

    def fit(
        self,
        tax: float,
        year: int,
        savings: float,
        interest_rate: float,
    ) -> "SavingsForTaxOptimizer":
        taxes = {
            date(year + 1, self.payment_month, self.payment_day): tax,
            date(year + 2, self.payment_month, self.payment_day): 0.0,
        }
        fun = partial(
            self._estimate_future_abs_savings,
            interest_rate=interest_rate,
            taxes=taxes,
        )
        res = minimize(fun=fun, x0=savings, method="Nelder-Mead")
        required_cash = res.x[0]
        final_cash = self._estimate_future_abs_savings(
            initial_savings=required_cash,
            taxes=taxes,
            interest_rate=interest_rate,
        )
        assert np.isclose(final_cash, 0.0, atol=self.tolerance)
        msg = f"Current savings: {savings:,.2f} PLN. Required savings: {required_cash:,.2f} PLN."
        if (diff := required_cash - savings) < -5e-3:
            msg = f"{msg} Decrease savings by:\t{np.abs(diff):,.2f} PLN."
        elif diff > 5e-3:
            msg = f"{msg} Increase savings by:\t{diff:,.2f} PLN."
        else:
            msg = f"{msg} Savings are sufficient."
        self.required_cash_ = required_cash
        self.msg_ = msg
        return self

    def _estimate_future_abs_savings(
        self,
        initial_savings: float,
        interest_rate: float,
        taxes: dict[date, float],
    ) -> float:
        savings = initial_savings
        taxes = taxes.copy()
        curr_date = datetime.today().date()
        max_date = max(taxes)
        while curr_date + timedelta(days=self.delay) < max_date:
            curr_date += timedelta(days=1)
            curr_tax_date = date(
                curr_date.year + 1, self.payment_month, self.payment_day
            )
            daily_savings = max(savings * interest_rate / 365, 0.0)
            savings += daily_savings
            daily_tax = daily_savings * self.tax_rate
            if curr_tax_date in taxes:
                taxes[curr_tax_date] += daily_tax
            else:
                taxes[curr_tax_date] = daily_tax
            savings -= taxes.get(curr_date + timedelta(days=self.delay), 0.0)
        return np.abs(savings)
