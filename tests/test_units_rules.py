from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.money import format_money, to_minor
from app.schemas import UnitIn, UnitQuery
from app.services.units import InvalidTransition, check_transition

VALID = {"project": "برج", "number": "A-1", "type": "شقة",
         "area_sqm": "100.50", "rooms": "3", "price": "1500000.50"}


def test_to_minor_is_exact():
    assert to_minor(Decimal("1500000.50")) == 150000050
    assert to_minor(Decimal("0.07")) == 7


def test_to_minor_rejects_sub_halala():
    with pytest.raises(ValueError):
        to_minor(Decimal("10.001"))


def test_format_money():
    assert format_money(150000050) == "1,500,000.50"
    assert format_money(5) == "0.05"


def test_unit_in_valid():
    assert UnitIn(**VALID).price == Decimal("1500000.50")


@pytest.mark.parametrize("field,value", [
    ("price", "10.001"), ("price", "-5"), ("price", "abc"), ("area_sqm", "0"),
    ("rooms", "-1"), ("rooms", "x"), ("project", "   "),
])
def test_unit_in_rejects_bad_values(field, value):
    with pytest.raises(ValidationError):
        UnitIn(**{**VALID, field: value})


def test_unit_in_rejects_mass_assignment():
    with pytest.raises(ValidationError):
        UnitIn(**VALID, status="sold")
    with pytest.raises(ValidationError):
        UnitIn(**VALID, company_id="2")


@pytest.mark.parametrize("params", [
    {"sort": "id; drop table units"}, {"page": "0"}, {"page": "-3"},
    {"per_page": "9999"}, {"status": "hacked"},
    {"min_price": "500", "max_price": "100"}, {"unknown": "1"},
])
def test_query_rejects_bad_params(params):
    with pytest.raises(ValidationError):
        UnitQuery(**params)


def test_query_defaults():
    q = UnitQuery()
    assert (q.page, q.per_page, q.sort) == (1, 10, "price")


@pytest.mark.parametrize("a,b", [("available", "reserved"), ("reserved", "available"),
                                 ("reserved", "sold")])
def test_allowed_transitions(a, b):
    check_transition(a, b)


@pytest.mark.parametrize("a,b", [("available", "sold"), ("sold", "available"),
                                 ("sold", "reserved"), ("available", "available")])
def test_forbidden_transitions(a, b):
    with pytest.raises(InvalidTransition):
        check_transition(a, b)