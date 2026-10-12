"""Append-only audit trail: who did what to which object, with readable labels and field diffs."""

from decimal import Decimal

from django.db.models import Model

from payroll.models import AuditLog


def label_for(obj) -> str:
    """A human name for the audited object, stored with the entry so it survives deletes."""
    kind = obj.__class__.__name__
    if kind == "Employee":
        return f"{obj.employee_number} {obj.first_name} {obj.last_name}"
    if kind == "PayrollRun":
        return f"{obj.pay_period.start_date:%b %Y} run"
    if kind == "PayPeriod":
        return f"{obj.start_date:%b %Y}"
    if kind == "DeductionType":
        return obj.code
    if kind == "TaxTable":
        return obj.name
    if kind == "TimesheetEntry":
        return f"{obj.employee.first_name} {obj.employee.last_name}"
    if kind == "EmployeeDeduction":
        return f"{obj.deduction_type.code} for {obj.employee.employee_number} {obj.employee.first_name}"
    if kind == "Company":
        return obj.name
    return f"{kind} #{obj.pk}"


def _display(value):
    """JSON-safe value for storage."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, Model):
        return value.pk
    if isinstance(value, list | tuple):
        return [_display(v) for v in value]
    if isinstance(value, dict):
        return {k: _display(v) for k, v in value.items()}
    return str(value)


def _compare(value):
    """Canonical form for deciding whether a value really changed (72000 == 72000.00)."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, Model):
        return value.pk
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, list | tuple):
        return [_compare(v) for v in value]
    if isinstance(value, dict):
        return {k: _compare(v) for k, v in value.items() if v is not None}
    return str(value)


def _current(instance, key):
    value = getattr(instance, key)
    if hasattr(value, "all"):  # reverse relation such as a tax table's brackets
        return [
            {f.name: getattr(row, f.name) for f in row._meta.fields if f.name not in ("id", "table")}
            for row in value.all()
        ]
    return value


def changed_fields(instance, new_values: dict) -> dict:
    """{field: {"old": ..., "new": ...}} for the fields whose value really changes."""
    changes = {}
    for key, new in new_values.items():
        old = _current(instance, key)
        if _compare(old) != _compare(new):
            changes[key] = {"old": _display(old), "new": _display(new)}
    return changes


def created_fields(values: dict) -> dict:
    """{field: {"new": ...}} for a newly created object."""
    return {key: {"new": _display(value)} for key, value in values.items()}


def record(*, company, actor, action, obj, changes=None, label=None):
    """Append one audit entry. Called from services and viewsets."""
    return AuditLog.objects.create(
        company=company,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        object_label=label or label_for(obj),
        changes=changes or {},
    )
