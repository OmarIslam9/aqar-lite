"""توزيع الدفعة على الأقساط: دالة نقية (بدون قاعدة بيانات)، كل المبالغ أعداد صحيحة بالهللات."""
from dataclasses import dataclass
from typing import Sequence


class ExceedsRemaining(ValueError):
    def __init__(self, remaining_minor: int):
        super().__init__("payment exceeds the remaining contract balance")
        self.remaining_minor = remaining_minor


@dataclass(frozen=True)
class Open:
    """لقطة من قسط وقت الدفع."""
    id: int
    seq: int
    amount_minor: int
    paid_minor: int


def remaining_total(items: Sequence[Open]) -> int:
    return sum(i.amount_minor - i.paid_minor for i in items)


def allocate(amount_minor: int, items: Sequence[Open]) -> list[tuple[int, int]]:
    """يرجّع [(installment_id, المبلغ المطبّق)] بترتيب الأقدم أولاً (seq تصاعدياً).
    الأقساط المسددة تُتخطى، وأي مبلغ زائد عن المتبقي يرفع ExceedsRemaining."""
    if amount_minor <= 0:
        raise ValueError("amount must be positive")
    remaining = remaining_total(items)
    if amount_minor > remaining:
        raise ExceedsRemaining(remaining)

    left = amount_minor
    plan: list[tuple[int, int]] = []
    for item in sorted(items, key=lambda i: i.seq):
        room = item.amount_minor - item.paid_minor
        if room <= 0:
            continue
        take = min(room, left)
        plan.append((item.id, take))
        left -= take
        if left == 0:
            break
    return plan