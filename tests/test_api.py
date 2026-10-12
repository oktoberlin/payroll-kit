import io
from decimal import Decimal as D

import pytest
from django.urls import reverse

from payroll.models import Employee, PayrollRun
from payroll.services import runs
from tests.factories import CompanyFactory, EmployeeFactory, MembershipFactory, PayPeriodFactory

pytestmark = pytest.mark.django_db


def test_jwt_login(company):
    m = MembershipFactory(company=company)
    from rest_framework.test import APIClient

    resp = APIClient().post(
        reverse("token_obtain_pair"), {"username": m.user.username, "password": "pw-12345"}, format="json"
    )
    assert resp.status_code == 200 and "access" in resp.data


def test_unauthenticated_is_rejected(client):
    assert client.get("/api/employees/").status_code == 401


def test_openapi_schema_builds(make_client, admin):
    resp = make_client(admin).get("/api/schema/")
    assert resp.status_code == 200


def test_full_run_flow_over_api(make_client, admin, period, staff):
    c = make_client(admin)
    run_id = c.post("/api/payroll-runs/", {"pay_period": period.id}, format="json").data["id"]
    resp = c.post(f"/api/payroll-runs/{run_id}/calculate/")
    assert resp.status_code == 200 and resp.data["payslip_count"] == 2
    assert resp["X-Recalculated"] == "true"
    assert c.post(f"/api/payroll-runs/{run_id}/calculate/")["X-Recalculated"] == "false"
    assert c.post(f"/api/payroll-runs/{run_id}/approve/").data["status"] == "approved"
    assert c.post(f"/api/payroll-runs/{run_id}/mark-paid/").data["status"] == "paid"
    assert c.delete(f"/api/payroll-runs/{run_id}/").status_code == 400
    assert c.get("/api/audit-logs/").data["count"] >= 4


def test_duplicate_run_for_period_rejected(make_client, admin, period):
    c = make_client(admin)
    assert c.post("/api/payroll-runs/", {"pay_period": period.id}, format="json").status_code == 201
    assert c.post("/api/payroll-runs/", {"pay_period": period.id}, format="json").status_code == 400


def test_payslip_pdf(make_client, admin, company, period, staff):
    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    slip = run.payslips.first()
    resp = make_client(admin).get(f"/api/payslips/{slip.id}/pdf/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_csv_import_all_or_nothing(make_client, manager, company):
    c = make_client(manager)
    good = (
        "employee_number,first_name,last_name,email,pay_type,annual_salary,hourly_rate\n"
        "A1,Ann,One,a@example.com,salary,50000,\n"
        "A2,Bo,Two,b@example.com,hourly,,19.50\n"
    )
    resp = c.post("/api/employees/import/", {"file": io.BytesIO(good.encode())}, format="multipart")
    assert resp.status_code == 201 and resp.data["created"] == 2

    bad = (
        "employee_number,first_name,last_name,email,pay_type,annual_salary,hourly_rate\n"
        "A3,Cy,Three,c@example.com,salary,40000,\n"
        "A1,Dup,Four,d@example.com,salary,40000,\n"
        "A4,Ed,Five,e@example.com,weekly,,\n"
    )
    resp = c.post("/api/employees/import/", {"file": io.BytesIO(bad.encode())}, format="multipart")
    assert resp.status_code == 400
    assert [e["row"] for e in resp.data["errors"]] == [3, 4]
    assert Employee.objects.filter(company=company).count() == 2  # nothing from the bad file


def test_salary_employee_requires_salary(make_client, admin):
    resp = make_client(admin).post(
        "/api/employees/",
        {
            "employee_number": "X1",
            "first_name": "A",
            "last_name": "B",
            "email": "a@b.co",
            "pay_type": "salary",
        },
        format="json",
    )
    assert resp.status_code == 400 and "annual_salary" in resp.data


def test_cannot_reference_other_company_period(make_client, admin):
    other_period = PayPeriodFactory(company=CompanyFactory())
    resp = make_client(admin).post("/api/payroll-runs/", {"pay_period": other_period.id}, format="json")
    assert resp.status_code == 400


def test_employee_list_is_tenant_scoped(make_client, admin, staff):
    EmployeeFactory(company=CompanyFactory())  # someone else's employee
    resp = make_client(admin).get("/api/employees/")
    assert resp.data["count"] == 2


def test_negative_net_returns_400_not_500(make_client, admin, company, period):
    from payroll.models import DeductionType, EmployeeDeduction

    emp = EmployeeFactory(company=company, annual_salary=D("1200"))
    loan = DeductionType.objects.create(company=company, code="LOAN", name="Loan", default_value=D("500"))
    EmployeeDeduction.objects.create(employee=emp, deduction_type=loan)
    c = make_client(admin)
    run_id = c.post("/api/payroll-runs/", {"pay_period": period.id}, format="json").data["id"]
    resp = c.post(f"/api/payroll-runs/{run_id}/calculate/")
    assert resp.status_code == 400
    assert "negative" in str(resp.data)
    assert PayrollRun.objects.get(pk=run_id).payslips.count() == 0


def test_deleting_protected_objects_returns_409(make_client, admin, company, period, staff):
    from payroll.models import TaxTable

    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run)
    c = make_client(admin)
    assert c.delete(f"/api/employees/{staff[0].id}/").status_code == 409
    assert c.delete(f"/api/pay-periods/{period.id}/").status_code == 409
    table = TaxTable.objects.create(company=company, name="T")
    staff[1].tax_table = table
    staff[1].save()
    assert c.delete(f"/api/tax-tables/{table.id}/").status_code == 409


def _table(brackets):
    return {"name": "T", "brackets": brackets}


@pytest.mark.parametrize(
    "brackets",
    [
        [{"lower": "0", "upper": "100", "rate": "1.5"}],
        [{"lower": "50", "upper": "50", "rate": "0.1"}],
        [{"lower": "0", "upper": "100", "rate": "0.1"}, {"lower": "50", "upper": None, "rate": "0.2"}],
        [{"lower": "0", "upper": None, "rate": "0.1"}, {"lower": "100", "upper": None, "rate": "0.2"}],
    ],
)
def test_invalid_tax_brackets_rejected(make_client, admin, brackets):
    resp = make_client(admin).post("/api/tax-tables/", _table(brackets), format="json")
    assert resp.status_code == 400


def test_valid_tax_table_accepted(make_client, admin):
    ok = [{"lower": "0", "upper": "100", "rate": "0"}, {"lower": "100", "upper": None, "rate": "0.2"}]
    assert make_client(admin).post("/api/tax-tables/", _table(ok), format="json").status_code == 201


def test_overlapping_pay_period_rejected(make_client, admin, period):
    body = {
        "start_date": "2026-09-15",
        "end_date": "2026-10-14",
        "pay_date": "2026-10-15",
        "periods_per_year": 12,
    }
    assert make_client(admin).post("/api/pay-periods/", body, format="json").status_code == 400
    body.update(start_date="2026-10-01", end_date="2026-10-31", pay_date="2026-11-01")
    assert make_client(admin).post("/api/pay-periods/", body, format="json").status_code == 201


def test_health_endpoint_is_public(client):
    resp = client.get("/api/health/")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_token_endpoint_is_rate_limited(company, monkeypatch):
    from django.core.cache import cache
    from rest_framework.test import APIClient
    from rest_framework.throttling import ScopedRateThrottle

    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"token": "2/min"})
    c = APIClient()
    codes = [
        c.post(reverse("token_obtain_pair"), {"username": "x", "password": "y"}).status_code for _ in range(3)
    ]
    assert codes == [401, 401, 429]
    cache.clear()
