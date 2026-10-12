from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from payroll.models import AuditLog, PayrollRun, TimesheetEntry
from payroll.services import runs
from tests.factories import EmployeeFactory, UserFactory


@pytest.fixture
def run(company, period, staff):
    return PayrollRun.objects.create(company=company, pay_period=period)


def test_calculate_creates_payslips(run):
    run, changed = runs.calculate_run(run)
    assert changed is True
    assert run.payslips.count() == 2
    assert all(p.gross == D("5000.00") for p in run.payslips.all())


def test_calculate_is_idempotent(run):
    run, _ = runs.calculate_run(run)
    first_ids = list(run.payslips.values_list("id", flat=True))
    run2, changed = runs.calculate_run(run)
    assert changed is False
    assert list(run2.payslips.values_list("id", flat=True)) == first_ids
    assert AuditLog.objects.filter(action="payroll_run.calculated").count() == 1


def test_recalculates_when_inputs_change(run, company, period):
    runs.calculate_run(run)
    hourly = EmployeeFactory(company=company, pay_type="hourly", hourly_rate=D("20"), annual_salary=0)
    TimesheetEntry.objects.create(employee=hourly, pay_period=period, regular_hours=D("10"))
    run, changed = runs.calculate_run(run)
    assert changed is True
    assert run.payslips.get(employee=hourly).gross == D("200.00")


def test_approve_requires_fresh_calculation(run, company):
    user = UserFactory()
    runs.calculate_run(run)
    EmployeeFactory(company=company)  # inputs changed after calculation
    with pytest.raises(ValidationError, match="recalculate"):
        runs.approve_run(run, user)


def test_lifecycle_and_immutability(run):
    user = UserFactory()
    runs.calculate_run(run)
    runs.approve_run(run, user)
    with pytest.raises(ValidationError):
        runs.calculate_run(run)  # approved runs cannot be recalculated
    run = runs.mark_paid(run, user)
    assert run.status == PayrollRun.PAID
    with pytest.raises(ValidationError):
        runs.reopen_run(run, user)
    with pytest.raises(ValidationError, match="immutable"):
        run.save()
    with pytest.raises(ValidationError):
        run.delete()
    slip = run.payslips.first()
    slip.net = D("1.00")
    with pytest.raises(ValidationError):
        slip.save()
