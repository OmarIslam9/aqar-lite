import random
from datetime import date

import pytest

from app.services.schedule import (add_months, build_schedule, check_terms,
                                   installment_status, min_final_price)


def test_add_months_clamps_to_month_end_from_original_day():
    start = date(2026, 12, 31)
    assert add_months(start, 1) == date(2027, 1, 31)
    assert add_months(start, 2) == date(2027, 2, 28)     # مش سلسلة: يرجع 31 في مارس
    assert add_months(start, 3) == date(2027, 3, 31)
    assert add_months(start, 4) == date(2027, 4, 30)
    assert add_months(date(2027, 12, 31), 2) == date(2028, 2, 29)   # سنة كبيسة
    assert add_months(date(2026, 12, 15), 13) == date(2028, 1, 15)


def test_contract_starting_jan_31_is_due_end_of_february():
    items = build_schedule(final_minor=3000, down_minor=0, count=3, start_date=date(2027, 1, 31))
    assert [i.due_date for i in items] == [date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]


def test_schedule_always_sums_exactly():
    rng = random.Random(2026)
    for _ in range(2000):
        final = rng.randint(2, 10**12)
        down = rng.randint(0, final - 1)
        count = rng.randint(1, min(360, final - down))
        items = build_schedule(final_minor=final, down_minor=down, count=count,
                               start_date=date(2026, 1, 31))
        assert sum(i.amount_minor for i in items) == final
        assert all(i.amount_minor > 0 for i in items)
        assert len(items) == count + (1 if down else 0)


def test_remainder_goes_to_last_installment():
    items = build_schedule(final_minor=1000, down_minor=0, count=3, start_date=date(2026, 3, 1))
    assert [i.amount_minor for i in items] == [333, 333, 334]


def test_down_payment_is_seq_zero_on_start_date():
    items = build_schedule(final_minor=1000, down_minor=100, count=4, start_date=date(2026, 3, 10))
    assert (items[0].seq, items[0].due_date, items[0].amount_minor) == (0, date(2026, 3, 10), 100)
    assert [i.amount_minor for i in items[1:]] == [225, 225, 225, 225]


def test_schedule_rejects_impossible_input():
    with pytest.raises(ValueError):
        build_schedule(final_minor=1000, down_minor=995, count=6, start_date=date(2026, 1, 1))
    with pytest.raises(ValueError):
        build_schedule(final_minor=1000, down_minor=0, count=0, start_date=date(2026, 1, 1))


def test_discount_boundary_is_exact():
    listed = 1_000_000
    assert min_final_price(listed) == 950_000
    assert check_terms(list_minor=listed, final_minor=950_000, down_minor=0, count=12) == {}
    assert "final_price" in check_terms(list_minor=listed, final_minor=949_999, down_minor=0, count=12)
    assert "final_price" in check_terms(list_minor=listed, final_minor=listed + 1, down_minor=0, count=12)


def test_min_final_price_rounds_up():
    assert min_final_price(333) == 317       # 316.35 تُقرَّب لأعلى


def test_terms_down_payment_and_count_limits():
    assert "down_payment" in check_terms(list_minor=1000, final_minor=1000, down_minor=1000, count=3)
    assert "installments_count" in check_terms(list_minor=1000, final_minor=1000, down_minor=995, count=6)


def test_installment_status():
    today = date(2026, 6, 15)
    kw = dict(amount_minor=100, today=today)
    assert installment_status(paid_minor=100, due_date=date(2026, 1, 1), **kw) == "paid"
    assert installment_status(paid_minor=0, due_date=date(2026, 6, 14), **kw) == "overdue"
    assert installment_status(paid_minor=40, due_date=date(2026, 6, 14), **kw) == "overdue"
    assert installment_status(paid_minor=0, due_date=today, **kw) == "pending"   # يوم الاستحقاق مش متأخر
    assert installment_status(paid_minor=40, due_date=date(2026, 7, 1), **kw) == "partial"