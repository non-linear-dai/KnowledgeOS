"""Currency quote integrity checks; rates remain source-backed assertions."""
from __future__ import annotations

import re

from .core import ValidationError
from .models import decimal


def validate_exchange_rate(assertion, currencies=None):
    qualifiers = assertion.get("qualifiers") or {}
    base = qualifiers.get("base_currency")
    quote = qualifiers.get("quote_currency")
    rate_type = qualifiers.get("rate_type")
    if not all(isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code) for code in (base, quote)) or base == quote:
        raise ValidationError("exchange_rate requires distinct uppercase base_currency and quote_currency")
    if currencies is not None and (base not in currencies or quote not in currencies):
        raise ValidationError("exchange_rate references an unregistered currency")
    if rate_type not in ("mid", "bid", "ask", "official"):
        raise ValidationError("exchange_rate requires a supported rate_type")
    value = assertion.get("value") or {}
    if not isinstance(value, dict) or value.get("unit") != f"{quote}_per_{base}" or decimal(value.get("literal")) <= 0:
        raise ValidationError("exchange_rate value must use quote_per_base and be positive")
