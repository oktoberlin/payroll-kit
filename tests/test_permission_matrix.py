"""Every resource x role x HTTP method, plus cross-tenant writes."""

import pytest
from rest_framework.test import APIClient

from payroll.models import DeductionType, EmployeeDeduction, PayrollRun, TaxTable, TimesheetEntry
from payroll.services import runs
from tests.factories import CompanyFactory, EmployeeFactory, PayPeriodFactory, UserFactory

pytestmark = pytest.mark.django_db

STAFF_RESOURCES = [
    "employees",
    "tax-tables",
    "deduction-types",
    "employee-deductions",
    "pay-periods",
    "timesheets",
    "payroll-runs",
]


@pytest.fixture
def other_company_objects():
    """One object per staff resource, all belonging to a different company."""
    other = CompanyFactory()
    emp = EmployeeFactory(company=other)
    period = PayPeriodFactory(company=other)
    dtype = DeductionType.objects.create(company=other, code="X", name="X")
    return {
        "employees": emp,
        "tax-tables": TaxTable.objects.create(company=other, name="T"),
        "deduction-types": dtype,
        "employee-deductions": EmployeeDeduction.objects.create(employee=emp, deduction_type=dtype),
        "pay-periods": period,
        "timesheets": TimesheetEntry.objects.create(employee=emp, pay_period=period),
        "payroll-runs": PayrollRun.objects.create(company=other, pay_period=period),
    }


def _anonymous_member():
    client = APIClient()
    client.force_authenticate(UserFactory())  # logged in, but no company membership
    return client


@pytest.mark.parametrize("resource", STAFF_RESOURCES)
@pytest.mark.parametrize("method", ["get", "post"])
def test_employees_and_non_members_cannot_use_staff_resources(make_client, employee_member, resource, method):
    for client in (make_client(employee_member), _anonymous_member()):
        assert getattr(client, method)(f"/api/{resource}/", {}, format="json").status_code == 403


@pytest.mark.parametrize("resource", STAFF_RESOURCES)
@pytest.mark.parametrize("method", ["get", "patch", "put", "delete"])
def test_employees_cannot_touch_staff_resource_detail(
    make_client, employee_member, other_company_objects, company, resource, method
):
    url = f"/api/{resource}/{other_company_objects[resource].pk}/"
    assert getattr(make_client(employee_member), method)(url, {}, format="json").status_code == 403


@pytest.mark.parametrize("resource", STAFF_RESOURCES)
@pytest.mark.parametrize("role", ["admin", "manager"])
@pytest.mark.parametrize("method", ["get", "patch", "put", "delete"])
def test_staff_cannot_reach_another_companys_objects(
    make_client, request, other_company_objects, resource, role, method
):
    client = make_client(request.getfixturevalue(role))
    url = f"/api/{resource}/{other_company_objects[resource].pk}/"
    # 404 (hidden), or 405 where the method does not exist for that resource; never 200/204.
    assert getattr(client, method)(url, {}, format="json").status_code in (404, 405)


def test_run_actions_on_another_companys_run_are_404(make_client, admin, other_company_objects):
    run = other_company_objects["payroll-runs"]
    c = make_client(admin)
    for action in ("calculate", "approve", "reopen", "mark-paid"):
        assert c.post(f"/api/payroll-runs/{run.pk}/{action}/").status_code == 404
    assert c.get(f"/api/payroll-runs/{run.pk}/export/").status_code == 404


def test_payslips_are_read_only_for_everyone(
    make_client, admin, manager, employee_member, company, period, staff
):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    slip = run.payslips.first()
    for member in (admin, manager):
        c = make_client(member)
        assert c.post("/api/payslips/", {}, format="json").status_code == 405
        assert c.patch(f"/api/payslips/{slip.pk}/", {}, format="json").status_code == 405
        assert c.delete(f"/api/payslips/{slip.pk}/").status_code == 405
    assert make_client(employee_member).post("/api/payslips/", {}, format="json").status_code == 403


def test_admin_only_actions_forbidden_to_managers(make_client, manager, company, period, staff):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    c = make_client(manager)
    for action in ("approve", "reopen", "mark-paid"):
        assert c.post(f"/api/payroll-runs/{run.pk}/{action}/").status_code == 403


def test_cannot_create_records_that_reference_another_company(
    make_client, admin, other_company_objects, company
):
    c = make_client(admin)
    other_emp = other_company_objects["employees"]
    other_period = other_company_objects["pay-periods"]
    other_type = other_company_objects["deduction-types"]
    mine = EmployeeFactory(company=company)
    assert (
        c.post(
            "/api/timesheets/", {"employee": other_emp.pk, "pay_period": other_period.pk}, format="json"
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/employee-deductions/", {"employee": mine.pk, "deduction_type": other_type.pk}, format="json"
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/employee-deductions/",
            {"employee": other_emp.pk, "deduction_type": other_type.pk},
            format="json",
        ).status_code
        == 400
    )
