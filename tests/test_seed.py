from decimal import Decimal as D

import pytest
from django.core.management import call_command

from payroll.models import AuditLog, Company, PayPeriod, PayrollRun, Payslip
from payroll.services.ytd import ytd_totals

pytestmark = pytest.mark.django_db


def test_seed_demo_is_repeatable():
    call_command("seed_demo")
    call_command("seed_demo")  # second call is a no-op
    call_command("seed_demo", "--reset")
    assert Company.objects.count() == 1
    assert Payslip.objects.count() == 54  # 6 employees x (8 paid months + the September draft)


def test_seed_demo_has_paid_history_a_draft_run_and_an_open_period():
    call_command("seed_demo")
    runs = PayrollRun.objects.order_by("pay_period__start_date")
    assert [r.status for r in runs] == ["paid"] * 8 + ["draft"]
    assert PayPeriod.objects.count() == 10  # Jan-Sep runs plus an October period with no run
    assert PayPeriod.objects.filter(runs__isnull=True).get().start_date.isoformat() == "2026-10-01"
    sep = runs.last()
    assert sep.pay_period.pay_date.isoformat() == "2026-10-01"
    assert sep.calculated_by.username == "demo_manager"
    assert all(
        r.approved_by.username == "demo_admin" and r.paid_by.username == "demo_admin" for r in runs[:8]
    )


def test_seed_demo_figures_match_the_documented_worked_example():
    call_command("seed_demo")
    sep = PayrollRun.objects.get(pay_period__start_date__month=9)
    ava = sep.payslips.get(employee__employee_number="E001")
    assert (ava.gross, ava.taxable, ava.tax, ava.net) == (
        D("6000.00"),
        D("5580.00"),
        D("690.93"),
        D("4889.07"),
    )
    assert ytd_totals(ava) == {"gross": D("54000.00"), "tax": D("6218.37"), "net": D("44001.63")}


def test_seed_demo_history_is_audited_and_the_demo_employee_has_payslips():
    call_command("seed_demo")
    assert AuditLog.objects.filter(action="payroll_run.paid").count() == 8
    assert AuditLog.objects.filter(action="employees.imported", object_label="6 employees").exists()
    dev = Payslip.objects.filter(employee__user__username="demo_employee", run__status="paid")
    assert dev.count() == 8
