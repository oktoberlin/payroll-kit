"""More pure calculation tests, including a property test that every payslip adds up."""

from decimal import Decimal as D

import pytest
from hypothesis import given
from hypothesis import strategies as st

from payroll.services.calculation import (
    BENEFIT,
    CENT,
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
)

BRACKETS = (
    TaxBracket(D("0"), D("10000"), D("0")),
    TaxBracket(D("10000"), D("40000"), D("0.10")),
    TaxBracket(D("40000"), None, D("0.20")),
)


def test_bracket_boundaries_are_continuous():
    assert annual_tax(D("10000"), BRACKETS) == 0
    assert annual_tax(D("10000.01"), BRACKETS) == D("0.001")
    assert annual_tax(D("40000"), BRACKETS) == D("3000")
    assert annual_tax(D("40000.01"), BRACKETS) == D("3000.002")


@pytest.mark.parametrize("periods, expected", [(24, "2500.00"), (26, "2307.69"), (52, "1153.85")])
def test_salary_for_each_frequency(periods, expected):
    r = calculate_pay(PayInput(pay_type=SALARY, periods_per_year=periods, annual_salary=D("60000")))
    assert r.gross == D(expected)


def test_percent_deduction_on_hourly_pay():
    r = calculate_pay(
        PayInput(
            pay_type=HOURLY,
            periods_per_year=52,
            hourly_rate=D("20"),
            regular_hours=D("40"),
            overtime_hours=D("5"),
            deductions=(DeductionRule("RET", "Retirement", PERCENT, D("0.05"), pre_tax=True),),
        )
    )
    assert r.gross == D("950.00")
    assert r.pre_tax_deductions == D("47.50")  # 5% of regular + overtime pay
    assert r.net == D("902.50")


def test_benefit_only_employee_net_is_gross():
    r = calculate_pay(
        PayInput(
            pay_type=SALARY,
            periods_per_year=12,
            annual_salary=D("60000"),
            deductions=(DeductionRule("MATCH", "Match", PERCENT, D("0.03"), kind=BENEFIT),),
        )
    )
    assert r.employer_benefits == D("150.00") and r.net == r.gross == D("5000.00")


def test_proration_applies_to_salary_only():
    half = calculate_pay(
        PayInput(pay_type=SALARY, periods_per_year=12, annual_salary=D("60000"), proration=D("0.5"))
    )
    assert half.gross == D("2500.00") and "prorated" in half.lines[0].description
    hourly = calculate_pay(
        PayInput(
            pay_type=HOURLY,
            periods_per_year=12,
            hourly_rate=D("20"),
            regular_hours=D("10"),
            proration=D("0.5"),
        )
    )
    assert hourly.gross == D("200.00")


@pytest.mark.parametrize("value", [D("-0.1"), D("1.1")])
def test_invalid_proration_rejected(value):
    with pytest.raises(PayrollCalculationError, match="proration"):
        calculate_pay(
            PayInput(pay_type=SALARY, periods_per_year=12, annual_salary=D("1000"), proration=value)
        )


cents = st.decimals(min_value=0, max_value=500_000, places=2)
rules = st.builds(
    DeductionRule,
    code=st.sampled_from(["A", "B", "C"]),
    name=st.just("rule"),
    method=st.sampled_from([FIXED, PERCENT]),
    value=st.one_of(st.decimals(min_value=0, max_value=500, places=2), st.decimals(0, "0.3", places=4)),
    pre_tax=st.booleans(),
    kind=st.sampled_from(["deduction", BENEFIT]),
)


@given(
    pay_type=st.sampled_from([SALARY, HOURLY]),
    periods=st.sampled_from([12, 24, 26, 52]),
    salary=cents,
    rate=st.decimals(min_value=0, max_value=200, places=2),
    regular=st.decimals(min_value=0, max_value=300, places=2),
    overtime=st.decimals(min_value=0, max_value=100, places=2),
    multiplier=st.decimals(min_value=1, max_value=3, places=2),
    proration=st.decimals(min_value=0, max_value=1, places=4),
    deductions=st.lists(rules, max_size=4),
)
def test_payslip_always_adds_up_to_the_cent(
    pay_type, periods, salary, rate, regular, overtime, multiplier, proration, deductions
):
    inputs = PayInput(
        pay_type=pay_type,
        periods_per_year=periods,
        annual_salary=salary,
        hourly_rate=rate,
        regular_hours=regular,
        overtime_hours=overtime,
        overtime_multiplier=multiplier,
        proration=proration,
        deductions=tuple(deductions),
        tax_brackets=BRACKETS,
    )
    try:
        r = calculate_pay(inputs)
    except PayrollCalculationError:
        return  # negative net / pre-tax above gross is a valid, explicit error
    assert all(ln.amount == ln.amount.quantize(CENT) for ln in r.lines)
    assert r.net == r.gross - r.pre_tax_deductions - r.tax - r.post_tax_deductions
    assert r.net >= 0
    # employer benefits never change take-home pay
    without_benefits = PayInput(
        **{**inputs.__dict__, "deductions": tuple(d for d in deductions if d.kind != BENEFIT)}
    )
    assert calculate_pay(without_benefits).net == r.net
