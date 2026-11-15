"""المبالغ: Decimal عند الحدود، وint بالهللات داخل النظام وقاعدة البيانات. لا float أبداً."""
from decimal import Decimal

_HUNDRED = Decimal(100)


def to_minor(amount: Decimal) -> int:
    """يحوّل مبلغاً بوحدة العملة إلى هللات، ويرفض أكثر من خانتين عشريتين."""
    scaled = amount * _HUNDRED
    if scaled != scaled.to_integral_value():
        raise ValueError("amount has more than 2 decimal places")
    return int(scaled)


def format_money(minor: int) -> str:
    whole, frac = divmod(abs(int(minor)), 100)
    return f"{'-' if minor < 0 else ''}{whole:,}.{frac:02d}"


def minor_to_input(minor: int) -> str:
    """قيمة تُعرض داخل حقل الإدخال (بدون فواصل آلاف)."""
    whole, frac = divmod(int(minor), 100)
    return f"{whole}.{frac:02d}"