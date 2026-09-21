"""Money primitives: half-up rounding, exact multiplication, exact parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.shared.money import (
    apply_percentage_change,
    decimal_to_cents,
    multiply_cents,
    round_cents,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.4", 0),
        ("0.5", 1),          # exact half rounds away from zero
        ("0.6", 1),
        ("1.5", 2),
        ("2.5", 3),          # NOT banker's rounding, which would give 2
        ("-0.5", -1),
        ("-1.5", -2),
        ("99.5", 100),
        ("100.49999", 100),
    ],
)
def test_round_cents_half_up(value, expected):
    assert round_cents(Decimal(value)) == expected



def test_multiply_cents_rounds_once_half_up():
    assert multiply_cents(3333, Decimal("1.125")) == 3750      # 3749.625
    assert multiply_cents(1, Decimal("0.5")) == 1               # exact half
    assert multiply_cents(10000, Decimal(1)) == 10000


def test_percentage_change_up_and_down():
    assert apply_percentage_change(10000, Decimal("0.20")) == 12000
    assert apply_percentage_change(10000, Decimal("-0.10")) == 9000
    assert apply_percentage_change(151975, Decimal("-0.12")) == 133738   # 133738.0
    assert apply_percentage_change(12345, Decimal(0)) == 12345


@pytest.mark.parametrize(
    "text,cents",
    [("0", 0), ("1301.25", 130125), ("6079.00", 607900), ("0.5", 50), ("12", 1200)],
)
def test_decimal_to_cents_is_exact(text, cents):
    assert decimal_to_cents(text) == cents
    assert isinstance(decimal_to_cents(text), int)


@pytest.mark.parametrize("text", ["100.005", "-1.00", "abc", "", "NaN", "Infinity"])
def test_decimal_to_cents_rejects_what_is_not_whole_cents(text):
    with pytest.raises(ValueError):
        decimal_to_cents(text)
