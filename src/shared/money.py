"""Money primitives: integer cents, exact decimals, half-up rounding.

Every monetary value in this project is an ``int`` number of cents.  Anything
that can produce a fraction of a cent goes through ``decimal.Decimal`` and is
rounded here, half away from zero, back to a whole cent.  No binary float ever
touches money.

This module knows nothing about any contract.  *Which* multipliers apply, in
what order, and how often to round are contract terms and live with the
hospital that has them.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_cents(value: Decimal) -> int:
    """Half up, away from zero, to a whole cent.

    ``Decimal.quantize(ROUND_HALF_UP)`` rounds half away from zero for negative
    values too (``-0.5 -> -1``), and never rounds to even (``2.5 -> 3``).
    """
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def multiply_cents(cents: int, factor: Decimal) -> int:
    """``cents * factor``, computed exactly, then rounded once to a whole cent."""
    return round_cents(Decimal(cents) * factor)


def apply_percentage_change(cents: int, fraction: Decimal) -> int:
    """Raise (positive ``fraction``) or lower (negative) an amount, then round.

    ``apply_percentage_change(10000, Decimal("0.20")) == 12000`` is a 20%
    uplift; ``Decimal("-0.10")`` is a 10% discount.
    """
    return multiply_cents(cents, Decimal(1) + fraction)


def decimal_to_cents(amount: str) -> int:
    """``"1301.25" -> 130125``, exactly.

    Raises ``ValueError`` for anything that is not a plain non-negative decimal
    or that does not come to a whole number of cents.  Stripping a currency
    symbol or thousands separators is the caller's job: that is document
    formatting, not money.
    """
    try:
        value = Decimal(amount)
    except Exception as exc:  # decimal.InvalidOperation
        raise ValueError(f"not a decimal amount: {amount!r}") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"not a non-negative decimal amount: {amount!r}")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"money amount is not a whole cent: {amount!r}")
    return int(cents)
