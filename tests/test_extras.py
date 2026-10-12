"""Proration in runs, year-to-date totals, the payroll register export and employee dates."""

import csv
import io
from datetime import date
from decimal import Decimal as D

import pytest

from payroll.models import AuditLog, PayrollRun
from payroll.services import runs
from payroll.services.export import _safe
from payroll.services.ytd import ytd_totals
from tests.factories import EmployeeFactory, PayPeriodFactory

pytestmark = pytest.mark.django_db


def test_mid_period_hire_is_prorated_and_before_hire_is_skipped(company, period):
    # period is 1-30 September (30 days). Hired 16 September -> 15/30.
    half = EmployeeFactory(company=company, hire_date=date(2026, 9, 16))
    later = EmployeeFactory(company=company, hire_date=date(2026, 10, 1))
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    assert run.payslips.get(employee=half).gross == D("2500.00")
    assert not run.payslips.filter(employee=later).exists()


def test_leaver_is_prorated(company, period):
    leaver = EmployeeFactory(company=company, termination_date=date(2026, 9, 10))
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    assert run.payslips.get(employee=leaver).gross == D("1666.67")  # 10/30 of 5000


def test_changing_hire_date_triggers_recalculation(company, period):
    emp = EmployeeFactory(company=company)
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    emp.hire_date = date(2026, 9, 16)
    emp.save()
    _, changed = runs.calculate_run(run)
    assert changed is True


def test_ytd_accumulates_over_approved_runs(company, admin):
    emp = EmployeeFactory(company=company)
    slips = []
    for month in (1, 2, 3):
        p = PayPeriodFactory(
            company=company,
            start_date=date(2026, month, 1),
            end_date=date(2026, month, 28),
            pay_date=date(2026, month, 28),
        )
        run = PayrollRun.objects.create(company=company, pay_period=p)
        runs.calculate_run(run)
        slips.append(run.payslips.get(employee=emp))
        if month < 3:
            runs.approve_run(run, admin.user)
    assert ytd_totals(slips[0])["gross"] == D("5000.00")
    assert ytd_totals(slips[2])["gross"] == D("15000.00")  # the draft run counts for itself


def test_ytd_ignores_unapproved_earlier_runs_and_other_years(company, admin):
    emp = EmployeeFactory(company=company)
    old = PayPeriodFactory(
        company=company,
        start_date=date(2025, 12, 1),
        end_date=date(2025, 12, 28),
        pay_date=date(2025, 12, 28),
    )
    draft = PayPeriodFactory(
        company=company, start_date=date(2026, 1, 1), end_date=date(2026, 1, 28), pay_date=date(2026, 1, 28)
    )
    current = PayPeriodFactory(
        company=company, start_date=date(2026, 2, 1), end_date=date(2026, 2, 28), pay_date=date(2026, 2, 28)
    )
    for p in (old, draft, current):
        runs.calculate_run(PayrollRun.objects.create(company=company, pay_period=p))
    old_run = PayrollRun.objects.get(pay_period=old)
    runs.approve_run(old_run, admin.user)  # approved, but last year
    slip = PayrollRun.objects.get(pay_period=current).payslips.get(employee=emp)
    assert ytd_totals(slip)["gross"] == D("5000.00")  # only the current payslip


def test_payslip_detail_has_ytd_but_list_does_not(make_client, admin, company, period, staff):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    slip = run.payslips.first()
    c = make_client(admin)
    assert c.get(f"/api/payslips/{slip.id}/").data["ytd"]["gross"] == "5000.00"
    assert "ytd" not in c.get("/api/payslips/").data["results"][0]
    assert c.get(f"/api/payslips/{slip.id}/pdf/").content.startswith(b"%PDF")


def test_register_export(make_client, manager, employee_member, company, period):
    EmployeeFactory(company=company, first_name="=cmd", last_name="Evil", employee_number="E1")
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    resp = make_client(manager).get(f"/api/payroll-runs/{run.id}/export/")
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.content.decode())))
    assert rows[0][0] == "employee_number" and rows[1][1] == "'=cmd Evil"
    assert rows[1][2] == "5000.00" and rows[1][-1] == "5000.00"
    assert AuditLog.objects.filter(action="payroll_run.exported").count() == 1
    assert make_client(employee_member).get(f"/api/payroll-runs/{run.id}/export/").status_code == 403


@pytest.mark.parametrize(
    "text, safe", [("=1+1", "'=1+1"), ("+x", "'+x"), ("-x", "'-x"), ("@x", "'@x"), ("ok", "ok")]
)
def test_csv_cells_neutralise_formulas(text, safe):
    assert _safe(text) == safe


def test_termination_before_hire_rejected(make_client, admin):
    body = {
        "employee_number": "D1",
        "first_name": "A",
        "last_name": "B",
        "email": "a@b.co",
        "pay_type": "salary",
        "annual_salary": "1000",
        "hire_date": "2026-05-01",
        "termination_date": "2026-04-01",
    }
    resp = make_client(admin).post("/api/employees/", body, format="json")
    assert resp.status_code == 400 and "termination_date" in resp.data
