import random
from typing import Optional


def calculate_backoff_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    max_exponent: int = 10,
) -> float:
    """Exponential backoff with jitter.

    Args:
        attempt: Zero-based attempt counter.
        base_delay: Base delay in seconds.
        max_delay: Maximum delay cap in seconds.
        max_exponent: Clamp the exponent to avoid overflow.

    Returns:
        Delay in seconds (always <= max_delay).
    """
    backoff_multiplier = 2 ** min(attempt, max_exponent)
    return min(base_delay * backoff_multiplier + random.uniform(0, 1), max_delay)


def format_reconnect_attempts(current: int, maximum: int) -> str:
    """Format reconnection attempt counter for logging.

    Args:
        current: Current attempt number (1-based).
        maximum: Maximum attempts allowed (0 means unlimited).

    Returns:
        String like ``"3/10"`` or ``"3/unlimited"``.
    """
    return f"{current}/{maximum}" if maximum > 0 else f"{current}/unlimited"


def parse_hex_or_int(value) -> Optional[int]:
    """Parse a value that may be a hex string, int, or None.

    Args:
        value: A hex string (``"0x1a"``), plain int, or None.

    Returns:
        Parsed integer, or None if *value* is None.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16)
    return int(value)


class BoundedDedupSet:
    """A set that auto-clears when it exceeds *max_size* entries.

    Useful for deduplicating a stream of event keys without unbounded
    memory growth.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._seen: set[str] = set()
        self._max_size = max_size

    def check_and_add(self, key: str) -> bool:
        """Return True if *key* is new, False if it is a duplicate.

        When the set exceeds *max_size*, it is cleared before the new
        key is inserted.
        """
        if key in self._seen:
            return False
        if len(self._seen) >= self._max_size:
            self._seen.clear()
        self._seen.add(key)
        return True

    def clear(self) -> None:
        """Explicitly clear all tracked keys."""
        self._seen.clear()
