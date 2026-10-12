"""Pure calculation tests: no database needed."""

from decimal import Decimal as D

import pytest

from payroll.services.calculation import (
    BENEFIT,
    FIXED,
    HOURLY,
    PERCENT,
    SALARY,
    DeductionRule,
    PayInput,
    PayrollCalculationError,
    TaxBracket,
    annual_tax,
    calculate_pay,
    money,
)

BRACKETS = (
    TaxBracket(D("0"), D("10000"), D("0")),
    TaxBracket(D("10000"), D("40000"), D("0.10")),
    TaxBracket(D("40000"), None, D("0.20")),
)


def test_salary_monthly_gross():
    r = calculate_pay(PayInput(pay_type=SALARY, periods_per_year=12, annual_salary=D("60000")))
    assert r.gross == D("5000.00")
    assert r.net == D("5000.00")


def test_salary_rounds_half_up_per_period():
    # 50000 / 26 = 1923.0769... -> 1923.08
    r = calculate_pay(PayInput(pay_type=SALARY, periods_per_year=26, annual_salary=D("50000")))
    assert r.gross == D("1923.08")


def test_money_rounding_is_half_up_not_bankers():
    assert money(D("0.125")) == D("0.13")
    assert money(D("2.675")) == D("2.68")
    with pytest.raises(TypeError):
        money(2.675)


def test_hourly_with_overtime():
    r = calculate_pay(
        PayInput(
            pay_type=HOURLY,
            periods_per_year=52,
            hourly_rate=D("20.00"),
            regular_hours=D("40"),
            overtime_hours=D("5"),
        )
    )
    # 40 * 20 = 800; 5 * 30 = 150
    assert [ln.amount for ln in r.lines] == [D("800.00"), D("150.00")]
    assert r.gross == D("950.00")


def test_hourly_zero_hours_gives_zero_pay():
    r = calculate_pay(PayInput(pay_type=HOURLY, periods_per_year=52, hourly_rate=D("25")))
    assert r.gross == D("0.00") and r.net == D("0.00")


def test_progressive_brackets_annual():
    assert annual_tax(D("9000"), BRACKETS) == 0
    assert annual_tax(D("40000"), BRACKETS) == D("3000")
    assert annual_tax(D("57000"), BRACKETS) == D("6400")


def test_full_payslip_pre_and_post_tax():
    r = calculate_pay(
        PayInput(
            pay_type=SALARY,
            periods_per_year=12,
            annual_salary=D("60000"),
            tax_brackets=BRACKETS,
            deductions=(
                DeductionRule("RET", "Retirement", PERCENT, D("0.05"), pre_tax=True),
                DeductionRule("HEALTH", "Health", FIXED, D("100")),
                DeductionRule("MATCH", "Employer match", PERCENT, D("0.03"), kind=BENEFIT),
            ),
        )
    )
    assert r.gross == D("5000.00")
    assert r.pre_tax_deductions == D("250.00")
    assert r.taxable == D("4750.00")
    assert r.tax == D("533.33")  # (3000 + 17000 * 0.20) / 12
    assert r.post_tax_deductions == D("100.00")
    assert r.employer_benefits == D("150.00")  # recorded, not deducted
    assert r.net == D("4116.67")
    # lines always add up to the cent
    assert r.gross - r.pre_tax_deductions - r.tax - r.post_tax_deductions == r.net


def test_negative_net_is_an_error_not_clamped():
    with pytest.raises(PayrollCalculationError, match="negative"):
        calculate_pay(
            PayInput(
                pay_type=SALARY,
                periods_per_year=12,
                annual_salary=D("1200"),
                deductions=(DeductionRule("LOAN", "Loan", FIXED, D("500")),),
            )
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"periods_per_year": 10},
        {"pay_type": "commission"},
        {"annual_salary": D("-1")},
        {"overtime_multiplier": D("0.5")},
    ],
)
def test_invalid_inputs_rejected(kwargs):
    base = {"pay_type": SALARY, "periods_per_year": 12, "annual_salary": D("1000")}
    with pytest.raises(PayrollCalculationError):
        calculate_pay(PayInput(**{**base, **kwargs}))
