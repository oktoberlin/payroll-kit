"""Pure payroll calculation: no Django imports, no database, Decimal only.

Rules (kept deliberately simple and documented so a client can audit them):
- Every money amount is a Decimal, quantized to 0.01 with ROUND_HALF_UP.
- Each line is rounded individually; totals are sums of rounded lines,
  so the payslip always adds up to the cent.
- Salaried gross per period = annual_salary / periods_per_year, multiplied by the
  proration factor (days employed in the period / days in the period; 1 = full period).
  Hourly pay is never prorated: it is already based on hours worked.
- Hourly gross = regular_hours * rate + overtime_hours * rate * overtime_multiplier.
- Pre-tax deductions reduce taxable pay; post-tax deductions reduce net pay only.
- Tax uses the annualised bracket method: annualise taxable pay, apply the
  progressive brackets, divide by periods_per_year. Tax tables are ILLUSTRATIVE.
- Benefits (employer-paid) are recorded as lines but do not change net pay.
- A negative net pay is an error, never silently clamped.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

SALARY = "salary"
HOURLY = "hourly"

FIXED = "fixed"
PERCENT = "percent"

DEDUCTION = "deduction"
BENEFIT = "benefit"


class PayrollCalculationError(ValueError):
    """Raised when inputs are invalid or the result would be nonsensical."""


def money(value: Decimal | int | str) -> Decimal:
    """Quantize to cents with half-up rounding. Floats are rejected on purpose."""
    if isinstance(value, float):
        raise TypeError("Use Decimal or str for money, never float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxBracket:
    """Annual bracket: income above `lower` (and up to `upper`, None = no cap) taxed at `rate`."""

    lower: Decimal
    upper: Decimal | None
    rate: Decimal  # 0.10 = 10%


@dataclass(frozen=True)
class DeductionRule:
    code: str
    name: str
    method: str = FIXED  # fixed amount per period or percent of gross
    value: Decimal = ZERO  # amount, or rate (0.05 = 5%) when method == percent
    pre_tax: bool = False
    kind: str = DEDUCTION  # deduction (employee-paid) or benefit (employer-paid)


@dataclass(frozen=True)
class PayInput:
    pay_type: str
    periods_per_year: int
    annual_salary: Decimal = ZERO
    hourly_rate: Decimal = ZERO
    regular_hours: Decimal = ZERO
    overtime_hours: Decimal = ZERO
    overtime_multiplier: Decimal = Decimal("1.5")
    proration: Decimal = Decimal("1")  # share of the period employed, salary only
    deductions: tuple[DeductionRule, ...] = ()
    tax_brackets: tuple[TaxBracket, ...] = ()


@dataclass(frozen=True)
class Line:
    kind: str  # earning | pre_tax | tax | post_tax | benefit
    code: str
    description: str
    amount: Decimal


@dataclass
class PayResult:
    lines: list[Line] = field(default_factory=list)

    def _sum(self, *kinds: str) -> Decimal:
        return sum((ln.amount for ln in self.lines if ln.kind in kinds), ZERO)

    @property
    def gross(self) -> Decimal:
        return self._sum("earning")

    @property
    def pre_tax_deductions(self) -> Decimal:
        return self._sum("pre_tax")

    @property
    def taxable(self) -> Decimal:
        return self.gross - self.pre_tax_deductions

    @property
    def tax(self) -> Decimal:
        return self._sum("tax")

    @property
    def post_tax_deductions(self) -> Decimal:
        return self._sum("post_tax")

    @property
    def employer_benefits(self) -> Decimal:
        return self._sum("benefit")

    @property
    def net(self) -> Decimal:
        return self.gross - self.pre_tax_deductions - self.tax - self.post_tax_deductions


def annual_tax(annual_income: Decimal, brackets: Iterable[TaxBracket]) -> Decimal:
    """Progressive tax on an annual amount. Not rounded; caller rounds once."""
    total = Decimal("0")
    for b in sorted(brackets, key=lambda x: x.lower):
        if annual_income <= b.lower:
            continue
        top = annual_income if b.upper is None else min(annual_income, b.upper)
        total += (top - b.lower) * b.rate
    return total


def _validate(p: PayInput) -> None:
    if p.periods_per_year not in (12, 24, 26, 52):
        raise PayrollCalculationError("periods_per_year must be 12, 24, 26 or 52")
    if p.pay_type not in (SALARY, HOURLY):
        raise PayrollCalculationError(f"unknown pay_type {p.pay_type!r}")
    for name in ("annual_salary", "hourly_rate", "regular_hours", "overtime_hours"):
        if getattr(p, name) < 0:
            raise PayrollCalculationError(f"{name} cannot be negative")
    if p.overtime_multiplier < 1:
        raise PayrollCalculationError("overtime_multiplier must be >= 1")
    if not (0 <= p.proration <= 1):
        raise PayrollCalculationError("proration must be between 0 and 1")


def calculate_pay(p: PayInput) -> PayResult:
    _validate(p)
    result = PayResult()

    # 1. Earnings
    if p.pay_type == SALARY:
        label = "Salary" if p.proration == 1 else f"Salary (prorated {p.proration:.4f})"
        amount = money(p.annual_salary / p.periods_per_year * p.proration)
        result.lines.append(Line("earning", "SALARY", label, amount))
    else:
        result.lines.append(
            Line(
                "earning",
                "REGULAR",
                f"Regular {p.regular_hours}h @ {p.hourly_rate}",
                money(p.regular_hours * p.hourly_rate),
            )
        )
        if p.overtime_hours:
            rate = p.hourly_rate * p.overtime_multiplier
            result.lines.append(
                Line(
                    "earning",
                    "OVERTIME",
                    f"Overtime {p.overtime_hours}h @ {money(rate)}",
                    money(p.overtime_hours * rate),
                )
            )
    gross = result.gross

    def amount_for(rule: DeductionRule) -> Decimal:
        if rule.method == PERCENT:
            return money(gross * rule.value)
        if rule.method == FIXED:
            return money(rule.value)
        raise PayrollCalculationError(f"unknown deduction method {rule.method!r}")

    # 2. Pre-tax deductions and employer benefits
    for rule in p.deductions:
        if rule.kind == BENEFIT:
            result.lines.append(Line("benefit", rule.code, rule.name, amount_for(rule)))
        elif rule.pre_tax:
            result.lines.append(Line("pre_tax", rule.code, rule.name, amount_for(rule)))

    taxable = result.taxable
    if taxable < 0:
        raise PayrollCalculationError("pre-tax deductions exceed gross pay")

    # 3. Tax (annualised bracket method, rounded once per period)
    if p.tax_brackets:
        per_period = annual_tax(taxable * p.periods_per_year, p.tax_brackets) / p.periods_per_year
        result.lines.append(Line("tax", "TAX", "Income tax (illustrative table)", money(per_period)))

    # 4. Post-tax deductions
    for rule in p.deductions:
        if rule.kind == DEDUCTION and not rule.pre_tax:
            result.lines.append(Line("post_tax", rule.code, rule.name, amount_for(rule)))

    if result.net < 0:
        raise PayrollCalculationError(f"net pay would be negative ({result.net})")
    return result
