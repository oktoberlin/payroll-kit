from rest_framework.permissions import SAFE_METHODS, BasePermission

from payroll.models import Membership


def membership(request):
    return getattr(request.user, "membership", None) if request.user.is_authenticated else None


class IsPayrollStaff(BasePermission):
    """Admins and payroll managers: full access within their own company."""

    def has_permission(self, request, view):
        m = membership(request)
        return bool(m and m.role in (Membership.ADMIN, Membership.MANAGER))


class IsCompanyAdmin(BasePermission):
    def has_permission(self, request, view):
        m = membership(request)
        return bool(m and m.role == Membership.ADMIN)


class IsStaffOrReadOwnPayslip(BasePermission):
    """Staff see every payslip in the company; employees may only read their own."""

    def has_permission(self, request, view):
        m = membership(request)
        if not m:
            return False
        if m.role == Membership.EMPLOYEE:
            return request.method in SAFE_METHODS
        return True
