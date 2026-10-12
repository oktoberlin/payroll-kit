from payroll.models import AuditLog


def record(*, company, actor, action, obj, changes=None):
    """Append one audit entry. Called from services and viewsets."""
    return AuditLog.objects.create(
        company=company,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        changes=changes or {},
    )
