# market_simulator/utils.py

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

def safe_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """Safely convert a value to Decimal, returning default on failure."""
    if value is None:
        return default
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return default

def quantize_price(price: Decimal | None) -> Decimal | None:
    """Quantize price to 2 decimal places, ensuring minimum of 0.01."""
    if price is None:
        return None
    try:
        # Ensure price is not negative and quantize
        quantized_price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return max(Decimal("0.01"), quantized_price)
    except (InvalidOperation, TypeError):
        return None # Return None if quantization fails