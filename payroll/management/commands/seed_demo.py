"""Create a demo tenant with users, employees, deductions, a tax table and a calculated run.

python manage.py seed_demo            # idempotent: skips if the demo company exists
python manage.py seed_demo --reset    # delete and recreate the demo company
"""

import os
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from payroll.models import (
    Company,
    DeductionType,
    Employee,
    EmployeeDeduction,
    Membership,
    PayPeriod,
    PayrollRun,
    TaxBracket,
    TaxTable,
    TimesheetEntry,
)
from payroll.services.runs import calculate_run

DEMO_COMPANY = "Northwind Demo Co"
PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-pass-123")

EMPLOYEES = [
    # number, first, last, pay_type, annual_salary, hourly_rate, regular, overtime
    ("E001", "Ava", "Stone", "salary", D("72000"), D("0"), None, None),
    ("E002", "Ben", "Carter", "salary", D("54000"), D("0"), None, None),
    ("E003", "Chloe", "Diaz", "salary", D("96000"), D("0"), None, None),
    ("E004", "Dev", "Patel", "hourly", D("0"), D("22.50"), D("160"), D("12")),
    ("E005", "Ella", "Moore", "hourly", D("0"), D("18.75"), D("120"), D("0")),
    ("E006", "Finn", "Lee", "hourly", D("0"), D("31.00"), D("168"), D("6.5")),
]


class Command(BaseCommand):
    help = "Seed a demo company (fake data only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true")

    @transaction.atomic
    def handle(self, *args, reset=False, **options):
        User = get_user_model()
        existing = Company.objects.filter(name=DEMO_COMPANY).first()
        if existing and not reset:
            self.stdout.write("Demo company already exists (use --reset to rebuild).")
            return
        if existing:
            for run in existing.payroll_runs.all():
                run.payslips.all().delete()
            PayrollRun.objects.filter(company=existing).delete()  # queryset delete bypasses model guard
            Employee.objects.filter(company=existing).delete()
            existing.delete()
            User.objects.filter(username__in=["demo_admin", "demo_manager", "demo_employee"]).delete()

        company = Company.objects.create(name=DEMO_COMPANY, currency="USD")
        users = {}
        for username, role in [
            ("demo_admin", Membership.ADMIN),
            ("demo_manager", Membership.MANAGER),
            ("demo_employee", Membership.EMPLOYEE),
        ]:
            user = User.objects.create_user(username=username, password=PASSWORD)
            Membership.objects.create(user=user, company=company, role=role)
            users[role] = user

        table = TaxTable.objects.create(company=company, name="Illustrative progressive table")
        for lower, upper, rate in [
            (D("0"), D("12000"), D("0")),
            (D("12000"), D("50000"), D("0.12")),
            (D("50000"), D("100000"), D("0.22")),
            (D("100000"), None, D("0.30")),
        ]:
            TaxBracket.objects.create(table=table, lower=lower, upper=upper, rate=rate)

        retirement = DeductionType.objects.create(
            company=company,
            code="RET",
            name="Retirement 5%",
            method="percent",
            default_value=D("0.05"),
            pre_tax=True,
        )
        health = DeductionType.objects.create(
            company=company,
            code="HEALTH",
            name="Health plan",
            method="fixed",
            default_value=D("120.00"),
            pre_tax=True,
        )
        loan = DeductionType.objects.create(
            company=company,
            code="LOAN",
            name="Salary advance repayment",
            method="fixed",
            default_value=D("50.00"),
        )
        employer_match = DeductionType.objects.create(
            company=company,
            code="MATCH",
            name="Employer retirement match",
            kind="benefit",
            method="percent",
            default_value=D("0.03"),
        )

        period = PayPeriod.objects.create(
            company=company,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
            pay_date=date(2026, 10, 1),
            periods_per_year=12,
        )

        for number, first, last, pay_type, salary, rate, reg, ot in EMPLOYEES:
            emp = Employee.objects.create(
                company=company,
                employee_number=number,
                first_name=first,
                last_name=last,
                email=f"{first.lower()}@example.com",
                pay_type=pay_type,
                annual_salary=salary,
                hourly_rate=rate,
                tax_table=table,
            )
            for dtype in (retirement, health, employer_match):
                EmployeeDeduction.objects.create(employee=emp, deduction_type=dtype)
            if number == "E002":
                EmployeeDeduction.objects.create(employee=emp, deduction_type=loan)
            if reg is not None:
                TimesheetEntry.objects.create(
                    employee=emp, pay_period=period, regular_hours=reg, overtime_hours=ot
                )
            if number == "E004":
                emp.user = users[Membership.EMPLOYEE]
                emp.save()

        run = PayrollRun.objects.create(company=company, pay_period=period)
        calculate_run(run, actor=users[Membership.MANAGER])
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {DEMO_COMPANY}: 6 employees, 1 calculated draft run. "
                f"Logins demo_admin / demo_manager / demo_employee, password {PASSWORD!r}."
            )
        )
