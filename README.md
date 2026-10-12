# PayrollKit API

A multi-tenant payroll backend built with Django REST Framework: employees, pay periods, payroll runs with an approval workflow, configurable deductions and benefits, payslip PDFs, CSV import, an audit trail and role-based JWT auth.

> **Demo project.** All data is fake, and the tax tables are **illustrative only, not tax advice**.

![CI](https://github.com/oktoberlin/payroll-kit/actions/workflows/ci.yml/badge.svg)

## Try it

- Web UI: https://payroll.hookangles.com ([source](https://github.com/oktoberlin/payroll-kit-ui))
- Swagger UI: https://api.payroll.hookangles.com/api/docs/
- Demo logins (password `demo-pass-123`):

| User | Role | Can do |
|---|---|---|
| `demo_admin` | Admin | Everything, including approving runs, marking them paid and reading the audit log |
| `demo_manager` | Payroll manager | Manage employees, periods and timesheets; calculate runs |
| `demo_employee` | Employee | Read their own payslips (JSON and PDF) only |

1. `POST /api/auth/token/` with a username and password, then click **Authorize** in Swagger and paste `Bearer <access>`.
2. `GET /api/payroll-runs/` shows the seeded draft run. `POST /api/payroll-runs/{id}/calculate/` recalculates it. The `X-Recalculated: false` header means the inputs haven't changed, so nothing was rewritten.
3. `GET /api/payslips/{id}/pdf/` downloads a payslip PDF.

<!-- Screenshots / GIF: docs/swagger.png, docs/run-flow.gif, docs/payslip.png -->

## What it demonstrates

- **Money handled correctly.** Every amount is a `Decimal`, rounded half-up to the cent per line, so totals always add up. Floats are rejected.
- **Idempotent payroll runs.** The calculation inputs are hashed (SHA-256). Recalculating with the same inputs is a no-op, and approval is refused if the inputs changed after the last calculation.
- **Run lifecycle.** `draft → approved → paid`. Paid runs and their payslips are immutable, enforced in the model layer and not just the API.
- **Multi-tenancy.** Every query is scoped to the user's company, and cross-tenant references are rejected on write.
- **Roles.** Admins, payroll managers and employees are separated with DRF permission classes. Employees can only see their own payslips.
- **Audit log.** An append-only record of creates, updates, calculations, approvals, payments and imports.
- **CSV import.** Every row is validated, and the import is all-or-nothing with per-row error messages.
- **Proration and year-to-date.** Mid-period hires and leavers are prorated by calendar days (salary only), and single payslips and PDFs show year-to-date gross, tax and net.
- **Payroll register export.** `GET /api/payroll-runs/{id}/export/` returns a CSV with one row per payslip, safe against spreadsheet formula injection.
- **Hardened for production.** Debug is off by default, the app refuses to start with a placeholder secret key, static files are served by whitenoise, login is rate-limited, and the container runs as a non-root user.
- **Quality gates.** 170+ pytest tests (calculation edge cases, a hypothesis property test that every payslip adds up to the cent, lifecycle, a full role x endpoint permission matrix, tenant isolation, PDF and CSV), ruff, black, a migrations check, and GitHub Actions running against Postgres.

## Calculation rules

| Step | Rule |
|---|---|
| Salary | `annual_salary / periods_per_year` (12, 24, 26 or 52), times the proration factor |
| Proration | Days employed in the period / days in the period, from `hire_date` and `termination_date`. Applies to salary only. Employees not employed at all in the period get no payslip |
| Hourly | `regular_hours × rate + overtime_hours × rate × overtime_multiplier` (default 1.5) |
| Pre-tax deductions | Fixed amount or % of gross; they reduce taxable pay |
| Tax | Annualised progressive brackets from an illustrative table, divided per period |
| Post-tax deductions | Fixed amount or % of gross; they reduce net pay |
| Employer benefits | Recorded on the payslip, not deducted |
| Negative net | Raises an error; never silently clamped |

The core is a pure function, `payroll/services/calculation.py::calculate_pay`, with no Django or database dependency, so it is easy to test and review.

## Run locally

Debug is off by default, so set `DJANGO_DEBUG=1` for local development (the tests do this themselves).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py seed_demo
python manage.py runserver   # http://127.0.0.1:8000/api/docs/
pytest
```

With Docker (API + Postgres):

```bash
docker compose up --build
docker compose exec api python manage.py seed_demo
```

## API overview

| Endpoint | Methods | Who |
|---|---|---|
| `/api/auth/token/`, `/api/auth/token/refresh/` | POST | anyone |
| `/api/auth/me/` | GET (user, role, company, employee record) | any signed-in member |
| `/api/employees/` (+ `/import/` CSV) | CRUD | admin, manager |
| `/api/tax-tables/` (nested brackets) | CRUD | admin, manager |
| `/api/deduction-types/`, `/api/employee-deductions/` | CRUD | admin, manager |
| `/api/pay-periods/`, `/api/timesheets/` | CRUD | admin, manager |
| `/api/payroll-runs/` | list, create, delete draft | admin, manager |
| `/api/payroll-runs/{id}/calculate/` | POST | admin, manager |
| `/api/payroll-runs/{id}/export/` | GET (CSV payroll register) | admin, manager |
| `/api/payroll-runs/{id}/approve/`, `/reopen/`, `/mark-paid/` | POST | admin |
| `/api/payslips/`, `/api/payslips/{id}/pdf/` | GET | staff (all), employee (own) |
| `/api/audit-logs/` | GET | admin |
| `/api/schema/`, `/api/docs/`, `/api/health/` | GET | public |

Notes: users are shown by username everywhere (`calculated_by`, `approved_by`, `paid_by`, audit `actor`). Runs include `period`, exact `totals` and `is_stale` (a draft whose inputs changed). Employees only see payslips of approved or paid runs, and timesheets of a paid period are locked. Audit `changes` hold `{old, new}` per changed field. Errors in a run (for example a negative net pay) return 400 with a message, and deleting a record that other records still reference returns 409. Linking an employee to a login (`Employee.user`) is done by the seed command or the Django admin, not through the API.

## Deploy on a VPS

Two options. On a server that already runs nginx and Postgres (like a shared VPS), follow
[`deploy/SETUP.md`](deploy/SETUP.md): systemd + gunicorn + nginx, no Docker. On a dedicated host, use Docker:

The production stack is `docker-compose.prod.yml`: Caddy (automatic HTTPS) in front of gunicorn and Postgres 16. You need a server with Docker and a domain whose DNS A record points at it.

```bash
git clone <repo-url> payrollkit && cd payrollkit
cp .env.example .env     # set DJANGO_SECRET_KEY, DOMAIN and POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api python manage.py seed_demo
```

Then open `https://<DOMAIN>/api/docs/`. To keep a public demo from being left in a broken state, reset it nightly from the host crontab:

```cron
0 3 * * * cd /opt/payrollkit && docker compose -f docker-compose.prod.yml exec -T api python manage.py seed_demo --reset
```

`seed_demo --reset` deletes and recreates the demo company and the three demo users, so use it only on a demo database.

## Stack

Python 3.12, Django 5.2, DRF 3.18, SimpleJWT, drf-spectacular, django-filter, ReportLab, whitenoise, PostgreSQL 16, gunicorn, Caddy, Docker, GitHub Actions. Tests use pytest, factory-boy and hypothesis.
