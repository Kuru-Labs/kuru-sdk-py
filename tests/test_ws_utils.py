import pytest

from kuru_sdk_py.utils.ws_utils import (
    calculate_backoff_delay,
    format_reconnect_attempts,
    parse_hex_or_int,
    BoundedDedupSet,
)


# --- calculate_backoff_delay ---


class TestCalculateBackoffDelay:
    def test_first_attempt_near_base_delay(self):
        delay = calculate_backoff_delay(0, base_delay=1.0, max_delay=60.0)
        # 1.0 * 2^0 + jitter(0,1) => [1.0, 2.0)
        assert 1.0 <= delay < 2.0

    def test_increases_with_attempt(self):
        # attempt=3 => 1.0 * 2^3 = 8.0, plus jitter => [8.0, 9.0)
        delay = calculate_backoff_delay(3, base_delay=1.0, max_delay=60.0)
        assert 8.0 <= delay < 9.0

    def test_capped_at_max_delay(self):
        delay = calculate_backoff_delay(20, base_delay=1.0, max_delay=5.0)
        assert delay <= 5.0

    def test_max_exponent_clamping(self):
        # With max_exponent=3, attempt=100 should behave like attempt=3
        delay = calculate_backoff_delay(
            100, base_delay=1.0, max_delay=1000.0, max_exponent=3
        )
        # 1.0 * 2^3 + jitter => [8.0, 9.0)
        assert 8.0 <= delay < 9.0

    def test_jitter_in_range(self):
        # Run many times to check jitter contribution is in [0, 1)
        for _ in range(50):
            delay = calculate_backoff_delay(0, base_delay=0.0, max_delay=100.0)
            # base=0 * 1 + jitter => [0.0, 1.0)
            assert 0.0 <= delay < 1.0


# --- format_reconnect_attempts ---


class TestFormatReconnectAttempts:
    def test_finite_max(self):
        assert format_reconnect_attempts(3, 10) == "3/10"

    def test_unlimited(self):
        assert format_reconnect_attempts(3, 0) == "3/unlimited"

    def test_first_attempt(self):
        assert format_reconnect_attempts(1, 5) == "1/5"


# --- parse_hex_or_int ---


class TestParseHexOrInt:
    def test_none_returns_none(self):
        assert parse_hex_or_int(None) is None

    def test_plain_int(self):
        assert parse_hex_or_int(42) == 42

    def test_hex_string(self):
        assert parse_hex_or_int("0x1a") == 26

    def test_hex_string_uppercase(self):
        assert parse_hex_or_int("0x1A") == 26

    def test_hex_string_no_prefix(self):
        # "ff" is valid hex without 0x prefix when passed to int(x, 16)
        assert parse_hex_or_int("ff") == 255

    def test_zero(self):
        assert parse_hex_or_int(0) == 0

    def test_hex_zero(self):
        assert parse_hex_or_int("0x0") == 0


# --- BoundedDedupSet ---


class TestBoundedDedupSet:
    def test_new_key_returns_true(self):
        ds = BoundedDedupSet(max_size=10)
        assert ds.check_and_add("a") is True

    def test_duplicate_key_returns_false(self):
        ds = BoundedDedupSet(max_size=10)
        ds.check_and_add("a")
        assert ds.check_and_add("a") is False

    def test_auto_clear_at_max_size(self):
        ds = BoundedDedupSet(max_size=3)
        ds.check_and_add("a")
        ds.check_and_add("b")
        ds.check_and_add("c")
        # Set is now at max_size; next insert should clear first
        assert ds.check_and_add("d") is True
        # "a" was cleared, so it should be new again
        assert ds.check_and_add("a") is True

    def test_explicit_clear(self):
        ds = BoundedDedupSet(max_size=10)
        ds.check_and_add("a")
        ds.clear()
        assert ds.check_and_add("a") is True

    def test_many_keys(self):
        ds = BoundedDedupSet(max_size=100)
        for i in range(100):
            assert ds.check_and_add(str(i)) is True
        for i in range(100):
            assert ds.check_and_add(str(i)) is False
