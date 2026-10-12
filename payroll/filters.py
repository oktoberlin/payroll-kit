import django_filters

from payroll.models import AuditLog


class AuditLogFilter(django_filters.FilterSet):
    """?action=&object_type=&actor=<username>"""

    actor = django_filters.CharFilter(field_name="actor__username")

    class Meta:
        model = AuditLog
        fields = ["action", "object_type", "actor"]
