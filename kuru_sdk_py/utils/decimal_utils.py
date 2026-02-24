from decimal import Decimal
from typing import Union

DecimalLike = Union[float, int, str, Decimal]


def to_decimal(value: DecimalLike) -> Decimal:
    """Convert a numeric value to Decimal, avoiding float imprecision.

    Uses str(value) intermediary so that float inputs like 0.1
    become Decimal('0.1') instead of Decimal('0.1000000000000000055511151231257827021181583404541015625').

    Args:
        value: A float, int, str, or Decimal to convert.

    Returns:
        Exact Decimal representation.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
