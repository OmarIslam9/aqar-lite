from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


def field_errors(exc: ValidationError) -> dict[str, str]:
    """يحوّل أخطاء Pydantic لرسائل عربية عامة بجانب كل حقل."""
    out: dict[str, str] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "form"
        required = err["type"] in ("missing", "string_too_short")
        out.setdefault(field, "هذا الحقل مطلوب" if required else "قيمة غير صالحة")
    return out