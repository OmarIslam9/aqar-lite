import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator, model_validator)
from pydantic_core import PydanticCustomError

from .timeutil import today_local


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class UnitIn(BaseModel):
    """الحقول المسموح بها فقط: status و company_id غير موجودين عمداً (extra=forbid)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project: str = Field(min_length=1, max_length=200)
    number: str = Field(min_length=1, max_length=50)
    type: str = Field(min_length=1, max_length=50)
    area_sqm: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    rooms: int = Field(ge=0, le=50)
    price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class UnitQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "reserved", "sold"] | None = None
    min_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    sort: Literal["price", "-price", "area", "-area"] = "price"
    page: int = Field(default=1, ge=1, le=100000)
    per_page: int = Field(default=10, ge=5, le=50)

    @model_validator(mode="after")
    def _check_range(self):
        if (self.min_price is not None and self.max_price is not None
                and self.min_price > self.max_price):
            raise PydanticCustomError("price_range", "min_price is greater than max_price")
        return self


class ClientIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(pattern=r"^\+?[0-9]{7,15}$")
    notes: str = Field(default="", max_length=5000)

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_phone(cls, v):
        return re.sub(r"[\s\-()]", "", v) if isinstance(v, str) else v


class PageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=100000)
    per_page: int = Field(default=20, ge=5, le=50)


class ReserveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: int = Field(gt=0)


class ReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: int = Field(gt=0)
    client_id: int = Field(gt=0)


class ReservationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: int = Field(gt=0)


class ContractIn(BaseModel):
    """لا يحتوي status ولا company_id ولا الأسعار الأصلية: كلها تُحدَّد في الخادم."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reservation_id: int = Field(gt=0)
    final_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    down_payment: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    installments_count: int = Field(ge=1, le=360)
    start_date: date

    @field_validator("start_date")
    @classmethod
    def _start_in_range(cls, v: date):
        today = today_local()
        if not (today <= v <= today + timedelta(days=365)):
            raise PydanticCustomError("start_date_range", "start date out of range")
        return v


MESSAGES = {
    "missing": "هذا الحقل مطلوب",
    "string_too_short": "هذا الحقل مطلوب",
    "string_too_long": "النص أطول من المسموح",
    "string_pattern_mismatch": "رقم هاتف غير صالح",
    "greater_than": "يجب أن تكون القيمة أكبر من صفر",
    "greater_than_equal": "القيمة أقل من الحد المسموح",
    "less_than_equal": "القيمة أكبر من الحد المسموح",
    "decimal_max_places": "حتى خانتين عشريتين فقط",
    "decimal_max_digits": "الرقم كبير جداً",
    "decimal_parsing": "أدخل رقماً صحيحاً",
    "int_parsing": "أدخل رقماً صحيحاً",
    "literal_error": "قيمة غير مسموحة",
    "extra_forbidden": "حقل غير مسموح",
    "price_range": "الحد الأدنى أكبر من الحد الأقصى",
    "date_parsing": "أدخل تاريخاً صحيحاً (YYYY-MM-DD)",
    "date_from_datetime_parsing": "أدخل تاريخاً صحيحاً (YYYY-MM-DD)",
    "date_type": "أدخل تاريخاً صحيحاً (YYYY-MM-DD)",
    "start_date_range": "تاريخ البداية يجب أن يكون من اليوم وحتى سنة قادمة",
}


def field_errors(exc: ValidationError) -> dict[str, str]:
    """يحوّل أخطاء Pydantic لرسائل عربية عامة بجانب كل حقل."""
    out: dict[str, str] = {}
    for err in exc.errors():
        kind = err["type"]
        if err["loc"]:
            field = str(err["loc"][0])
        else:
            field = "max_price" if kind == "price_range" else "form"
        out.setdefault(field, MESSAGES.get(kind, "قيمة غير صالحة"))
    return out