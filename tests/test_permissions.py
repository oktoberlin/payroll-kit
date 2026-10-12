import pytest

from payroll.models import PayrollRun
from payroll.services import runs
from tests.factories import CompanyFactory, EmployeeFactory, PayPeriodFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def calculated_run(company, period, staff):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    return run


def test_employee_sees_only_own_payslip(make_client, employee_member, calculated_run, staff):
    mine = staff[0]
    mine.user = employee_member.user
    mine.save()
    c = make_client(employee_member)
    resp = c.get("/api/payslips/")
    assert resp.data["count"] == 1
    assert resp.data["results"][0]["employee"] == mine.id
    other = calculated_run.payslips.exclude(employee=mine).first()
    assert c.get(f"/api/payslips/{other.id}/").status_code == 404
    assert c.get(f"/api/payslips/{other.id}/pdf/").status_code == 404


@pytest.mark.parametrize(
    "url", ["/api/employees/", "/api/payroll-runs/", "/api/audit-logs/", "/api/tax-tables/"]
)
def test_employee_role_blocked_from_admin_endpoints(make_client, employee_member, url):
    assert make_client(employee_member).get(url).status_code == 403


def test_manager_can_calculate_but_not_approve(make_client, manager, period, staff, company):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    c = make_client(manager)
    assert c.post(f"/api/payroll-runs/{run.id}/calculate/").status_code == 200
    assert c.post(f"/api/payroll-runs/{run.id}/approve/").status_code == 403
    assert c.get("/api/audit-logs/").status_code == 403


def test_admin_cannot_touch_another_company(make_client, admin):
    other = CompanyFactory()
    other_emp = EmployeeFactory(company=other)
    other_run = PayrollRun.objects.create(company=other, pay_period=PayPeriodFactory(company=other))
    c = make_client(admin)
    assert c.get(f"/api/employees/{other_emp.id}/").status_code == 404
    assert c.post(f"/api/payroll-runs/{other_run.id}/calculate/").status_code == 404


def test_user_without_membership_is_forbidden(make_client, company):
    from rest_framework.test import APIClient

    from tests.factories import UserFactory

    c = APIClient()
    c.force_authenticate(UserFactory())
    assert c.get("/api/employees/").status_code == 403
    assert c.get("/api/payslips/").status_code == 403
