"""منطق جدول الأقساط: دوال نقية (بدون قاعدة بيانات ولا Flask) كي تُختبر بسهولة.
كل المبالغ أعداد صحيحة بالهللات. لا float في أي مكان."""
import calendar
from dataclasses import dataclass
from datetime import date

MAX_DISCOUNT_PERCENT = 5


@dataclass(frozen=True)
class ScheduleItem:
    seq: int            # 0 = الدفعة الأولى (المقدّم)، 1..n = الأقساط الشهرية
    due_date: date
    amount_minor: int


def add_months(start: date, months: int) -> date:
    """start + months مع قص اليوم لآخر يوم في الشهر. محسوب من التاريخ الأصلي دائماً."""
    index = start.year * 12 + (start.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))


def min_final_price(list_minor: int) -> int:
    """أقل سعر نهائي مسموح (تقريب لأعلى حتى لا نتجاوز 5% بجزء من الهللة)."""
    return (list_minor * (100 - MAX_DISCOUNT_PERCENT) + 99) // 100


def check_terms(*, list_minor: int, final_minor: int, down_minor: int, count: int) -> dict[str, str]:
    errors: dict[str, str] = {}
    if final_minor > list_minor:
        errors["final_price"] = "السعر النهائي لا يمكن أن يزيد عن سعر الوحدة"
    elif final_minor < min_final_price(list_minor):
        errors["final_price"] = f"الخصم يتجاوز الحد الأقصى ({MAX_DISCOUNT_PERCENT}%)"
    if down_minor >= final_minor:
        errors["down_payment"] = "الدفعة الأولى يجب أن تكون أقل من السعر النهائي"
    elif final_minor - down_minor < count:
        errors["installments_count"] = "عدد الأقساط كبير جداً مقارنة بالمبلغ المتبقي"
    return errors


def build_schedule(*, final_minor: int, down_minor: int, count: int, start_date: date) -> list[ScheduleItem]:
    """الدفعة الأولى تستحق في start_date، والأقساط شهرياً بعدها.
    المتبقي ÷ العدد، وباقي القسمة يُضاف على آخر قسط، فالمجموع = السعر النهائي بالظبط."""
    if count < 1:
        raise ValueError("count must be >= 1")
    remaining = final_minor - down_minor
    if down_minor < 0 or remaining < count:
        raise ValueError("invalid down payment or installments count")

    base, extra = divmod(remaining, count)
    items: list[ScheduleItem] = []
    if down_minor > 0:
        items.append(ScheduleItem(0, start_date, down_minor))
    for k in range(1, count + 1):
        amount = base + (extra if k == count else 0)
        items.append(ScheduleItem(k, add_months(start_date, k), amount))

    if sum(i.amount_minor for i in items) != final_minor:
        raise ValueError("schedule total does not match final price")
    return items


def installment_status(*, amount_minor: int, paid_minor: int, due_date: date, today: date) -> str:
    """paid / overdue / partial / pending. المتأخر له الأولوية على الجزئي."""
    if paid_minor >= amount_minor:
        return "paid"
    if due_date < today:
        return "overdue"
    if paid_minor > 0:
        return "partial"
    return "pending"