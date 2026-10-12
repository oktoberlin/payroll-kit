from datetime import date
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model

from payroll import models


class CompanyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Company

    name = factory.Sequence(lambda n: f"Company {n}")


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()

    username = factory.Sequence(lambda n: f"user{n}")
    password = factory.django.Password("pw-12345")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Membership

    user = factory.SubFactory(UserFactory)
    company = factory.SubFactory(CompanyFactory)
    role = models.Membership.ADMIN


class EmployeeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.Employee

    company = factory.SubFactory(CompanyFactory)
    employee_number = factory.Sequence(lambda n: f"E{n:04d}")
    first_name = "Test"
    last_name = factory.Sequence(lambda n: f"Person{n}")
    email = factory.LazyAttribute(lambda o: f"{o.last_name.lower()}@example.com")
    pay_type = models.Employee.SALARY
    annual_salary = Decimal("60000")


class PayPeriodFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.PayPeriod

    company = factory.SubFactory(CompanyFactory)
    start_date = date(2026, 9, 1)
    end_date = date(2026, 9, 30)
    pay_date = date(2026, 10, 1)
    periods_per_year = 12
