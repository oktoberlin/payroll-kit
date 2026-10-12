import pytest
from rest_framework.test import APIClient

from payroll.models import Membership
from tests.factories import CompanyFactory, EmployeeFactory, MembershipFactory, PayPeriodFactory


@pytest.fixture
def company(db):
    return CompanyFactory()


@pytest.fixture
def make_client():
    def _make(membership):
        client = APIClient()
        client.force_authenticate(membership.user)
        return client

    return _make


@pytest.fixture
def admin(company):
    return MembershipFactory(company=company, role=Membership.ADMIN)


@pytest.fixture
def manager(company):
    return MembershipFactory(company=company, role=Membership.MANAGER)


@pytest.fixture
def employee_member(company):
    return MembershipFactory(company=company, role=Membership.EMPLOYEE)


@pytest.fixture
def period(company):
    return PayPeriodFactory(company=company)


@pytest.fixture
def staff(company):
    """Two salaried employees in the company."""
    return [EmployeeFactory(company=company), EmployeeFactory(company=company)]
