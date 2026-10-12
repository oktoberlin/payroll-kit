from django.urls import path
from rest_framework.routers import DefaultRouter

from payroll import views

router = DefaultRouter()
router.register("employees", views.EmployeeViewSet, basename="employee")
router.register("tax-tables", views.TaxTableViewSet, basename="taxtable")
router.register("deduction-types", views.DeductionTypeViewSet, basename="deductiontype")
router.register("employee-deductions", views.EmployeeDeductionViewSet, basename="employeededuction")
router.register("pay-periods", views.PayPeriodViewSet, basename="payperiod")
router.register("timesheets", views.TimesheetEntryViewSet, basename="timesheet")
router.register("payroll-runs", views.PayrollRunViewSet, basename="payrollrun")
router.register("payslips", views.PayslipViewSet, basename="payslip")
router.register("audit-logs", views.AuditLogViewSet, basename="auditlog")

urlpatterns = [path("auth/me/", views.MeView.as_view(), name="me"), *router.urls]
