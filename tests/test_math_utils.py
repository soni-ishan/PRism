"""
Tests for math_utils module
============================
Comprehensive tests covering edge cases for the calculate_discount function.

Test classes:
  TestCalculateDiscount — tests for discount calculation with various inputs
"""

from __future__ import annotations

import pytest

from math_utils import calculate_discount


# ── TestCalculateDiscount ─────────────────────────────────────────────

class TestCalculateDiscount:
    """Tests for the calculate_discount function with edge cases."""

    def test_no_discount(self):
        """Should return original price when discount is 0%."""
        assert calculate_discount(100, 0) == 100.0

    def test_full_discount(self):
        """Should return 0 when discount is 100%."""
        assert calculate_discount(100, 100) == 0.0

    def test_half_discount(self):
        """Should return half price when discount is 50%."""
        assert calculate_discount(100, 50) == 50.0

    def test_small_discount(self):
        """Should correctly calculate small discount percentages."""
        result = calculate_discount(100, 10)
        assert result == 90.0

    def test_large_discount(self):
        """Should correctly calculate large discount percentages."""
        result = calculate_discount(100, 90)
        assert result == pytest.approx(10.0)
    def test_zero_price(self):
        """Should return 0 when price is 0."""
        assert calculate_discount(0, 50) == 0.0

    def test_negative_price(self):
        """Should handle negative prices (edge case)."""
        result = calculate_discount(-100, 50)
        assert result == -50.0

    def test_decimal_price(self):
        """Should handle decimal prices correctly."""
        result = calculate_discount(99.99, 10)
        assert result == pytest.approx(89.991, rel=1e-5)

    def test_decimal_discount(self):
        """Should handle decimal discount percentages."""
        result = calculate_discount(100, 12.5)
        assert result == 87.5

    def test_small_decimal_discount(self):
        """Should handle very small discount percentages."""
        result = calculate_discount(100, 0.01)
        assert result == pytest.approx(99.99, rel=1e-5)

    def test_negative_discount_raises_error(self):
        """Should raise ValueError when discount is negative."""
        with pytest.raises(ValueError, match="Discount must be between 0 and 100"):
            calculate_discount(100, -1)

    def test_discount_above_100_raises_error(self):
        """Should raise ValueError when discount exceeds 100%."""
        with pytest.raises(ValueError, match="Discount must be between 0 and 100"):
            calculate_discount(100, 101)

    def test_discount_slightly_above_100_raises_error(self):
        """Should raise ValueError even for discount slightly above 100%."""
        with pytest.raises(ValueError, match="Discount must be between 0 and 100"):
            calculate_discount(100, 100.01)

    def test_large_negative_discount_raises_error(self):
        """Should raise ValueError for large negative discount."""
        with pytest.raises(ValueError, match="Discount must be between 0 and 100"):
            calculate_discount(100, -50)

    def test_boundary_at_zero_percent(self):
        """Should accept exactly 0% discount (lower boundary)."""
        result = calculate_discount(100, 0)
        assert result == 100.0

    def test_boundary_at_hundred_percent(self):
        """Should accept exactly 100% discount (upper boundary)."""
        result = calculate_discount(100, 100)
        assert result == 0.0

    def test_large_price_value(self):
        """Should handle very large price values."""
        result = calculate_discount(1000000, 25)
        assert result == 750000.0

    def test_very_small_price(self):
        """Should handle very small price values."""
        result = calculate_discount(0.01, 50)
        assert result == pytest.approx(0.005, rel=1e-5)

    def test_price_and_discount_both_zero(self):
        """Should return 0 when both price and discount are 0."""
        assert calculate_discount(0, 0) == 0.0

    def test_floating_point_precision(self):
        """Should maintain reasonable precision with floating point calculations."""
        result = calculate_discount(123.456, 7.89)
        expected = 123.456 * (1 - 7.89 / 100)
        assert result == pytest.approx(expected, rel=1e-9)
