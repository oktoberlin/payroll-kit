"""API behaviour the web UI relies on: /me, 400-not-500 validation, run/payslip/audit details."""

import pytest

from payroll.models import DeductionType, PayPeriod
from tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db

EMP = {
    "employee_number": "E002",
    "first_name": "New",
    "last_name": "Person",
    "email": "new@example.com",
    "pay_type": "salary",
    "annual_salary": "50000.00",
}


# --- A5: constraint violations are 400 field errors, not 500 -------------------------------------


def test_duplicate_employee_number_is_a_field_error(make_client, admin, company):
    EmployeeFactory(company=company, employee_number="E002", first_name="Ben", last_name="Carter")
    resp = make_client(admin).post("/api/employees/", EMP, format="json")
    assert resp.status_code == 400
    assert "Ben Carter" in str(resp.data["employee_number"])


def test_same_employee_number_in_another_company_is_fine(make_client, admin):
    from tests.factories import CompanyFactory

    EmployeeFactory(company=CompanyFactory(), employee_number="E002")
    assert make_client(admin).post("/api/employees/", EMP, format="json").status_code == 201


def test_editing_an_employee_does_not_clash_with_itself(make_client, admin, company):
    emp = EmployeeFactory(company=company, employee_number="E002")
    resp = make_client(admin).patch(f"/api/employees/{emp.pk}/", {"employee_number": "E002"}, format="json")
    assert resp.status_code == 200


def test_renaming_an_employee_to_a_taken_number_is_400(make_client, admin, company):
    EmployeeFactory(company=company, employee_number="E002")
    emp = EmployeeFactory(company=company, employee_number="E003")
    resp = make_client(admin).patch(f"/api/employees/{emp.pk}/", {"employee_number": "E002"}, format="json")
    assert resp.status_code == 400 and "employee_number" in resp.data


def test_duplicate_deduction_code_is_a_field_error(make_client, admin, company):
    DeductionType.objects.create(company=company, code="RET", name="Retirement")
    resp = make_client(admin).post(
        "/api/deduction-types/", {"code": "RET", "name": "Again", "default_value": "1.00"}, format="json"
    )
    assert resp.status_code == 400 and "code" in resp.data


def test_pay_period_with_same_dates_and_other_frequency_is_400(make_client, admin, period):
    body = {
        "start_date": str(period.start_date),
        "end_date": str(period.end_date),
        "pay_date": str(period.pay_date),
        "periods_per_year": 24,
    }
    assert make_client(admin).post("/api/pay-periods/", body, format="json").status_code == 400


def test_patching_a_pay_period_date_works(make_client, admin, period):
    resp = make_client(admin).patch(
        f"/api/pay-periods/{period.pk}/", {"pay_date": "2026-10-05"}, format="json"
    )
    assert resp.status_code == 200 and resp.data["pay_date"] == "2026-10-05"
    assert PayPeriod.objects.get(pk=period.pk).pay_date.isoformat() == "2026-10-05"


# --- A1: who am I -----------------------------------------------------------------------------


def test_me_for_staff(make_client, admin, company):
    resp = make_client(admin).get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data == {
        "id": admin.user.pk,
        "username": admin.user.username,
        "role": "admin",
        "company": {"id": company.pk, "name": company.name, "currency": "USD"},
        "employee": None,
    }


def test_me_for_an_employee_includes_the_employee_record(make_client, employee_member, company):
    emp = EmployeeFactory(company=company, user=employee_member.user, first_name="Dev", last_name="Patel")
    resp = make_client(employee_member).get("/api/auth/me/")
    assert resp.data["role"] == "employee"
    assert resp.data["employee"] == {
        "id": emp.pk,
        "employee_number": emp.employee_number,
        "first_name": "Dev",
        "last_name": "Patel",
    }


def test_me_requires_login_and_membership(client, make_client):
    from rest_framework.test import APIClient

    from tests.factories import UserFactory

    assert client.get("/api/auth/me/").status_code == 401
    no_member = APIClient()
    no_member.force_authenticate(UserFactory())
    assert no_member.get("/api/auth/me/").status_code == 403


# --- A2: run details ---------------------------------------------------------------------------


@pytest.fixture
def run(company, period, staff, manager):
    from payroll.models import PayrollRun
    from payroll.services import runs

    run = PayrollRun.objects.create(company=company, pay_period=period)
    runs.calculate_run(run, actor=manager.user)
    return run


def test_run_detail_has_period_totals_and_actors(make_client, admin, manager, run):
    data = make_client(admin).get(f"/api/payroll-runs/{run.pk}/").data
    assert data["period"] == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "pay_date": "2026-10-01",
        "periods_per_year": 12,
    }
    assert data["totals"] == {
        "gross": "10000.00",
        "pre_tax_deductions": "0.00",
        "tax": "0.00",
        "post_tax_deductions": "0.00",
        "employer_benefits": "0.00",
        "net": "10000.00",
    }
    assert data["calculated_by"] == manager.user.username
    assert data["approved_by"] is None and data["paid_by"] is None
    assert data["is_stale"] is False


def test_run_actors_are_recorded_through_the_lifecycle(make_client, admin, run):
    c = make_client(admin)
    assert c.post(f"/api/payroll-runs/{run.pk}/approve/").data["approved_by"] == admin.user.username
    paid = c.post(f"/api/payroll-runs/{run.pk}/mark-paid/").data
    assert paid["paid_by"] == admin.user.username and paid["approved_by"] == admin.user.username


def test_empty_run_has_zero_totals(make_client, admin, company, period):
    from payroll.models import PayrollRun

    empty = PayrollRun.objects.create(company=company, pay_period=period)
    data = make_client(admin).get(f"/api/payroll-runs/{empty.pk}/").data
    assert data["totals"]["net"] == "0.00" and data["calculated_by"] is None and data["is_stale"] is False


def test_run_is_stale_when_inputs_change_after_calculation(make_client, admin, company, run):
    from payroll.services import runs

    c = make_client(admin)
    EmployeeFactory(company=company)  # a new active employee changes the inputs
    assert c.get(f"/api/payroll-runs/{run.pk}/").data["is_stale"] is True
    assert c.get("/api/payroll-runs/").data["results"][0]["is_stale"] is True
    runs.calculate_run(run)
    assert c.get(f"/api/payroll-runs/{run.pk}/").data["is_stale"] is False


def test_approved_run_is_never_stale(make_client, admin, company, run):
    from payroll.services import runs

    runs.approve_run(run, admin.user)
    EmployeeFactory(company=company)
    assert make_client(admin).get(f"/api/payroll-runs/{run.pk}/").data["is_stale"] is False


def test_run_list_includes_totals_and_period(make_client, admin, run):
    row = make_client(admin).get("/api/payroll-runs/").data["results"][0]
    assert row["totals"]["net"] == "10000.00" and row["period"]["pay_date"] == "2026-10-01"


# --- A3: payslip details, employees only see approved/paid payslips -----------------------------


def test_payslip_has_employee_and_run_context(make_client, admin, run):
    slip = make_client(admin).get("/api/payslips/", {"run": run.pk}).data["results"][0]
    assert slip["employee_number"].startswith("E")
    assert slip["pay_type"] == "salary" and slip["annual_salary"] == "60000.00"
    assert slip["run_status"] == "draft"
    assert slip["period"] == {"start_date": "2026-09-01", "end_date": "2026-09-30", "pay_date": "2026-10-01"}


def test_employees_do_not_see_draft_payslips(make_client, employee_member, admin, run):
    from payroll.services import runs

    mine = run.payslips.first().employee
    mine.user = employee_member.user
    mine.save()
    c = make_client(employee_member)
    assert c.get("/api/payslips/").data["count"] == 0
    slip_id = run.payslips.get(employee=mine).pk
    assert c.get(f"/api/payslips/{slip_id}/").status_code == 404
    assert c.get(f"/api/payslips/{slip_id}/pdf/").status_code == 404
    runs.approve_run(run, admin.user)
    assert c.get("/api/payslips/").data["count"] == 1
    assert c.get(f"/api/payslips/{slip_id}/pdf/").status_code == 200


# --- A4: audit log is readable -----------------------------------------------------------------


def test_audit_entries_carry_username_label_and_diff(make_client, admin):
    c = make_client(admin)
    emp_id = c.post("/api/employees/", EMP, format="json").data["id"]
    c.patch(f"/api/employees/{emp_id}/", {"email": "changed@example.com"}, format="json")
    entries = c.get("/api/audit-logs/").data["results"]
    updated = next(e for e in entries if e["action"] == "updated")
    assert updated["actor"] == admin.user.username
    assert updated["object_label"] == "E002 New Person"
    assert updated["changes"] == {"email": {"old": "new@example.com", "new": "changed@example.com"}}
    created = next(e for e in entries if e["action"] == "created")
    assert created["changes"]["email"] == {"new": "new@example.com"}


def test_update_that_changes_nothing_is_not_logged(make_client, admin, company):
    from payroll.models import AuditLog

    emp = EmployeeFactory(company=company)
    make_client(admin).patch(f"/api/employees/{emp.pk}/", {"first_name": emp.first_name}, format="json")
    assert not AuditLog.objects.filter(action="updated").exists()


def test_audit_labels_for_runs_periods_and_deletes(make_client, admin, period):
    c = make_client(admin)
    run_id = c.post("/api/payroll-runs/", {"pay_period": period.pk}, format="json").data["id"]
    c.delete(f"/api/payroll-runs/{run_id}/")
    labels = {e["action"]: e["object_label"] for e in c.get("/api/audit-logs/").data["results"]}
    assert labels["created"] == "Sep 2026 run" and labels["deleted"] == "Sep 2026 run"


def test_audit_can_be_filtered_by_actor(make_client, admin, manager, company):
    make_client(manager).post("/api/deduction-types/", {"code": "A", "name": "A"}, format="json")
    make_client(admin).post("/api/deduction-types/", {"code": "B", "name": "B"}, format="json")
    c = make_client(admin)
    assert c.get("/api/audit-logs/").data["count"] == 2
    only = c.get("/api/audit-logs/", {"actor": manager.user.username}).data
    assert only["count"] == 1 and only["results"][0]["actor"] == manager.user.username


# --- A6: throttling uses the real client IP, but only from a trusted proxy ---------------------


def _login_codes(client, n, **headers):
    from django.urls import reverse

    url = reverse("token_obtain_pair")
    return [client.post(url, {"username": "x", "password": "y"}, **headers).status_code for _ in range(n)]


@pytest.fixture
def tight_token_limit(monkeypatch):
    from django.core.cache import cache

    from payroll.throttling import ProxyScopedRateThrottle

    cache.clear()
    monkeypatch.setattr(ProxyScopedRateThrottle, "THROTTLE_RATES", {"token": "2/min"})
    yield
    cache.clear()


def test_trusted_proxy_gives_each_client_ip_its_own_limit(tight_token_limit, settings):
    from rest_framework.test import APIClient

    settings.PROXY_IP_SECRET = "s3cret"
    c = APIClient()
    sig = {"HTTP_X_PROXY_SECRET": "s3cret"}
    assert _login_codes(c, 3, HTTP_X_CLIENT_IP="203.0.113.1", **sig) == [401, 401, 429]
    assert _login_codes(c, 2, HTTP_X_CLIENT_IP="203.0.113.2", **sig) == [401, 401]


def test_client_ip_header_is_ignored_without_the_secret(tight_token_limit, settings):
    from rest_framework.test import APIClient

    settings.PROXY_IP_SECRET = "s3cret"
    c = APIClient()
    forged = {"HTTP_X_PROXY_SECRET": "wrong"}
    assert _login_codes(c, 2, HTTP_X_CLIENT_IP="198.51.100.1", **forged) == [401, 401]
    # a different spoofed IP does not get a fresh allowance
    assert _login_codes(c, 1, HTTP_X_CLIENT_IP="198.51.100.2", **forged) == [429]


def test_client_ip_header_is_ignored_when_no_secret_is_configured(tight_token_limit, settings):
    from rest_framework.test import APIClient

    settings.PROXY_IP_SECRET = ""
    c = APIClient()
    assert _login_codes(c, 2, HTTP_X_CLIENT_IP="198.51.100.1") == [401, 401]
    assert _login_codes(c, 1, HTTP_X_CLIENT_IP="198.51.100.2") == [429]


# --- A7: timesheets of a paid period are locked ----------------------------------------------


def test_timesheets_of_a_paid_period_are_locked(make_client, admin, company, period, staff):
    from payroll.models import PayrollRun, TimesheetEntry
    from payroll.services import runs

    run = PayrollRun.objects.create(company=company, pay_period=period)
    entry = TimesheetEntry.objects.create(employee=staff[0], pay_period=period, regular_hours="10")
    runs.calculate_run(run)
    runs.approve_run(run, admin.user)
    c = make_client(admin)
    # approved but not paid: still editable (an admin can reopen), the UI only warns
    assert c.patch(f"/api/timesheets/{entry.pk}/", {"regular_hours": "11"}, format="json").status_code == 200
    runs.mark_paid(run, admin.user)
    assert c.patch(f"/api/timesheets/{entry.pk}/", {"regular_hours": "12"}, format="json").status_code == 400
    assert c.delete(f"/api/timesheets/{entry.pk}/").status_code == 400
    body = {"employee": staff[1].pk, "pay_period": period.pk, "regular_hours": "5"}
    resp = c.post("/api/timesheets/", body, format="json")
    assert resp.status_code == 400 and "locked" in str(resp.data)


# --- A8: employee search ------------------------------------------------------------------------


def test_employee_search(make_client, admin, company):
    EmployeeFactory(company=company, first_name="Ben", last_name="Carter", employee_number="E002")
    EmployeeFactory(company=company, first_name="Ava", last_name="Stone", employee_number="E001")
    c = make_client(admin)
    assert [e["last_name"] for e in c.get("/api/employees/", {"search": "carter"}).data["results"]] == [
        "Carter"
    ]
    assert c.get("/api/employees/", {"search": "E001"}).data["count"] == 1
    assert c.get("/api/employees/", {"search": "nobody"}).data["count"] == 0


def test_payslips_list_newest_period_first(make_client, employee_member, admin, company):
    from datetime import date

    from payroll.models import PayrollRun
    from payroll.services import runs
    from tests.factories import PayPeriodFactory

    emp = EmployeeFactory(company=company, user=employee_member.user)
    for month in (1, 3, 2):
        p = PayPeriodFactory(
            company=company,
            start_date=date(2026, month, 1),
            end_date=date(2026, month, 28),
            pay_date=date(2026, month, 28),
        )
        r = PayrollRun.objects.create(company=company, pay_period=p)
        runs.calculate_run(r)
        runs.approve_run(r, admin.user)
    dates = [
        s["period"]["start_date"] for s in make_client(employee_member).get("/api/payslips/").data["results"]
    ]
    assert dates == ["2026-03-01", "2026-02-01", "2026-01-01"] and emp
