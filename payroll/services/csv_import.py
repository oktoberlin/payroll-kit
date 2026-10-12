"""CSV employee import: validate every row, create all or nothing."""

import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction

from payroll.models import Employee
from payroll.services import audit

REQUIRED = ["employee_number", "first_name", "last_name", "email", "pay_type"]


def import_employees(company, fileobj, actor=None) -> dict:
    text = fileobj.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        return {"created": 0, "errors": [{"row": 1, "error": f"missing columns: {', '.join(missing)}"}]}

    existing = set(company.employees.values_list("employee_number", flat=True))
    to_create, errors, seen = [], [], set()
    for line_no, row in enumerate(reader, start=2):
        number = (row.get("employee_number") or "").strip()
        try:
            if not number:
                raise ValueError("employee_number is required")
            if number in existing or number in seen:
                raise ValueError(f"duplicate employee_number {number}")
            pay_type = row["pay_type"].strip().lower()
            if pay_type not in (Employee.SALARY, Employee.HOURLY):
                raise ValueError("pay_type must be salary or hourly")
            salary = Decimal(row.get("annual_salary") or "0")
            rate = Decimal(row.get("hourly_rate") or "0")
            if pay_type == Employee.SALARY and salary <= 0:
                raise ValueError("annual_salary must be > 0 for salary employees")
            if pay_type == Employee.HOURLY and rate <= 0:
                raise ValueError("hourly_rate must be > 0 for hourly employees")
        except (ValueError, InvalidOperation) as exc:
            errors.append({"row": line_no, "error": str(exc)})
            continue
        seen.add(number)
        to_create.append(
            Employee(
                company=company,
                employee_number=number,
                first_name=row["first_name"].strip(),
                last_name=row["last_name"].strip(),
                email=row["email"].strip(),
                pay_type=pay_type,
                annual_salary=salary,
                hourly_rate=rate,
            )
        )

    if errors:
        return {"created": 0, "errors": errors}
    with transaction.atomic():
        Employee.objects.bulk_create(to_create)
        audit.record(
            company=company,
            actor=actor,
            action="employees.imported",
            obj=company,
            label=f"{len(to_create)} employees",
            changes={"count": len(to_create)},
        )
    return {"created": len(to_create), "errors": []}
