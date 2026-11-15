import random

import pytest

from app.services.allocation import ExceedsRemaining, Open, allocate, remaining_total


def items(*pairs):
    """pairs = (amount, paid) بترتيب الاستحقاق."""
    return [Open(id=i + 1, seq=i, amount_minor=a, paid_minor=p) for i, (a, p) in enumerate(pairs)]


def test_pays_oldest_first_and_spills_over():
    assert allocate(250, items((100, 0), (100, 0), (100, 0))) == [(1, 100), (2, 100), (3, 50)]


def test_partial_payment_leaves_installment_partial():
    assert allocate(30, items((100, 0), (100, 0))) == [(1, 30)]


def test_continues_from_partially_paid_installment():
    assert allocate(80, items((100, 60), (100, 0))) == [(1, 40), (2, 40)]


def test_skips_fully_paid_installments():
    assert allocate(50, items((100, 100), (100, 0))) == [(2, 50)]


def test_input_order_does_not_matter():
    assert allocate(150, list(reversed(items((100, 0), (100, 0))))) == [(1, 100), (2, 50)]


def test_exact_remaining_is_ok_and_one_more_is_rejected():
    its = items((100, 20), (100, 0))
    assert sum(a for _, a in allocate(180, its)) == 180
    with pytest.raises(ExceedsRemaining) as exc:
        allocate(181, its)
    assert exc.value.remaining_minor == 180


@pytest.mark.parametrize("amount", [0, -1])
def test_rejects_non_positive_amounts(amount):
    with pytest.raises(ValueError):
        allocate(amount, items((100, 0)))


def test_random_invariants():
    rng = random.Random(7)
    for _ in range(2000):
        its = []
        for i in range(rng.randint(1, 12)):
            amount = rng.randint(1, 10**9)
            its.append(Open(i + 1, i, amount, rng.randint(0, amount)))
        remaining = remaining_total(its)
        if remaining == 0:
            continue
        pay = rng.randint(1, remaining)
        plan = allocate(pay, its)
        applied = dict(plan)
        by_id = {i.id: i for i in its}
        assert sum(applied.values()) == pay
        for installment_id, amount in plan:
            assert 0 < amount <= by_id[installment_id].amount_minor - by_id[installment_id].paid_minor
        last_seq = by_id[plan[-1][0]].seq
        for it in its:                       # كل قسط أقدم من آخر قسط موزَّع عليه لازم يتسدد بالكامل
            if it.seq < last_seq:
                assert it.paid_minor + applied.get(it.id, 0) == it.amount_minor