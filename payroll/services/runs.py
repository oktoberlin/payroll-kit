"""Payroll run lifecycle: calculate (idempotent) -> approve -> mark paid."""

import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from payroll.models import PayrollRun, Payslip, PayslipLine, TimesheetEntry
from payroll.services import audit
from payroll.services.calculation import DeductionRule, PayInput, TaxBracket, calculate_pay

TOTAL_FIELDS = [
    "gross",
    "pre_tax_deductions",
    "taxable",
    "tax",
    "post_tax_deductions",
    "employer_benefits",
    "net",
]


def run_totals(run: PayrollRun) -> dict[str, Decimal]:
    """Sum of every payslip column in the run, as Decimals (never floats)."""
    sums = run.payslips.aggregate(**{name: Sum(name) for name in TOTAL_FIELDS})
    return {name: sums[name] or Decimal("0.00") for name in TOTAL_FIELDS}


def is_stale(run: PayrollRun) -> bool:
    """A draft whose inputs changed since it was last calculated (it cannot be approved as is)."""
    if run.status != PayrollRun.DRAFT or not run.input_hash:
        return False
    return hash_inputs(build_inputs(run)) != run.input_hash


def proration_factor(employee, period) -> Decimal:
    """Share of the pay period the employee was employed, by calendar days (1 = whole period).

    0 means not employed at all in the period (hired after it ended, or left before it began).
    """
    start = max(filter(None, [period.start_date, employee.hire_date]))
    end = min(filter(None, [period.end_date, employee.termination_date]))
    if end < start:
        return Decimal("0")
    employed = (end - start).days + 1
    total = (period.end_date - period.start_date).days + 1
    return Decimal(employed) / Decimal(total)


def build_inputs(run: PayrollRun) -> dict[int, PayInput]:
    """Snapshot every active employee's pay inputs for this run's period."""
    period = run.pay_period
    hours = {
        t.employee_id: t
        for t in TimesheetEntry.objects.filter(pay_period=period, employee__company=run.company)
    }
    inputs = {}
    employees = (
        run.company.employees.filter(is_active=True)
        .select_related("tax_table")
        .prefetch_related("deductions__deduction_type", "tax_table__brackets")
        .order_by("pk")
    )
    for emp in employees:
        factor = proration_factor(emp, period)
        if factor == 0:
            continue  # not employed during this period: no payslip
        ts = hours.get(emp.pk)
        rules = tuple(
            DeductionRule(
                code=d.deduction_type.code,
                name=d.deduction_type.name,
                method=d.deduction_type.method,
                value=d.value if d.value is not None else d.deduction_type.default_value,
                pre_tax=d.deduction_type.pre_tax,
                kind=d.deduction_type.kind,
            )
            for d in sorted(emp.deductions.all(), key=lambda d: d.deduction_type.code)
        )
        brackets = (
            tuple(TaxBracket(b.lower, b.upper, b.rate) for b in emp.tax_table.brackets.all())
            if emp.tax_table
            else ()
        )
        inputs[emp.pk] = PayInput(
            pay_type=emp.pay_type,
            periods_per_year=period.periods_per_year,
            annual_salary=emp.annual_salary,
            hourly_rate=emp.hourly_rate,
            regular_hours=ts.regular_hours if ts else Decimal("0"),
            overtime_hours=ts.overtime_hours if ts else Decimal("0"),
            overtime_multiplier=emp.overtime_multiplier,
            proration=factor,
            deductions=rules,
            tax_brackets=brackets,
        )
    return inputs


def hash_inputs(inputs: dict[int, PayInput]) -> str:
    canonical = json.dumps({str(k): repr(v) for k, v in sorted(inputs.items())}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@transaction.atomic
def calculate_run(run: PayrollRun, actor=None) -> tuple[PayrollRun, bool]:
    """Calculate (or recalculate) a draft run.

    Idempotent: if inputs have not changed since the last calculation, nothing is
    written and the second value of the result is False.
    """
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.DRAFT:
        raise ValidationError("Only draft runs can be calculated.")

    inputs = build_inputs(run)
    digest = hash_inputs(inputs)
    if digest == run.input_hash and run.payslips.exists():
        return run, False

    run.payslips.all().delete()
    for employee_id, pay_input in inputs.items():
        result = calculate_pay(pay_input)
        slip = Payslip.objects.create(
            run=run,
            employee_id=employee_id,
            gross=result.gross,
            pre_tax_deductions=result.pre_tax_deductions,
            taxable=result.taxable,
            tax=result.tax,
            post_tax_deductions=result.post_tax_deductions,
            employer_benefits=result.employer_benefits,
            net=result.net,
        )
        PayslipLine.objects.bulk_create(
            PayslipLine(
                payslip=slip,
                kind=ln.kind,
                code=ln.code,
                description=ln.description,
                amount=ln.amount,
                position=i,
            )
            for i, ln in enumerate(result.lines)
        )

    run.input_hash = digest
    run.calculated_at = timezone.now()
    run.calculated_by = actor if getattr(actor, "is_authenticated", False) else None
    run.save()
    audit.record(
        company=run.company,
        actor=actor,
        action="payroll_run.calculated",
        obj=run,
        changes={"payslips": len(inputs), "input_hash": digest},
    )
    return run, True


@transaction.atomic
def approve_run(run: PayrollRun, actor) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.DRAFT:
        raise ValidationError("Only draft runs can be approved.")
    if not run.payslips.exists():
        raise ValidationError("Calculate the run before approving it.")
    if hash_inputs(build_inputs(run)) != run.input_hash:
        raise ValidationError("Inputs changed since the last calculation; recalculate first.")
    run.status = PayrollRun.APPROVED
    run.approved_by = actor
    run.approved_at = timezone.now()
    run.save()
    audit.record(company=run.company, actor=actor, action="payroll_run.approved", obj=run)
    return run


@transaction.atomic
def reopen_run(run: PayrollRun, actor) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.APPROVED:
        raise ValidationError("Only approved (unpaid) runs can be reopened.")
    run.status = PayrollRun.DRAFT
    run.approved_by = None
    run.approved_at = None
    run.save()
    audit.record(company=run.company, actor=actor, action="payroll_run.reopened", obj=run)
    return run


@transaction.atomic
def mark_paid(run: PayrollRun, actor) -> PayrollRun:
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status != PayrollRun.APPROVED:
        raise ValidationError("Only approved runs can be marked paid.")
    run.status = PayrollRun.PAID
    run.paid_at = timezone.now()
    run.paid_by = actor
    run.save()
    audit.record(company=run.company, actor=actor, action="payroll_run.paid", obj=run)
    return run
