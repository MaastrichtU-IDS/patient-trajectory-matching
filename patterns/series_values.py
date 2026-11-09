"""Measurement values for the execution store's `series` table (D27): exact decimals and micro-unit integers.

Pure and dependency-free, so the store can validate series rows without importing the claim machinery.
`decimal_of` accepts exactly what `measurement_claims.decimal_value` accepts (a test keeps the two equal);
`micro_value` is the value times 10**6 as an int when that is an exact integer within BIGINT, by integer
arithmetic on the decimal's digits and exponent, and None otherwise.

No decimal context is used: `Decimal.scaleb` rounds to the context's 28 digits, and the exported spellings
may have 64 characters, so '1000000000000.0000000000000000001' would come out as exactly 10**18. A value
outside BIGINT is None, never a refusal: the exact decimal text still holds it, and only comparisons on the
integer are lost. `decimal_text` is `format(Decimal, 'f')`, as `measurement_claims.select` renders values:
exact and scale-preserving, so not a value key ('1.5' and '1.50' differ); equality and order use
`micro_value`, or exact decimal arithmetic where it is None.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

BIGINT_MIN, BIGINT_MAX = -2 ** 63, 2 ** 63 - 1
_SPELLING = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


class SeriesValueError(ValueError):
    """A spelling outside the measurement decimal contract; the message is a stable code."""


def decimal_of(text):
    """The exact Decimal of an exported spelling, or SeriesValueError('INVALID_MEASUREMENT_DECIMAL' or
    'MEASUREMENT_DECIMAL_LIMIT'), as measurement_claims.decimal_value decides."""
    if not (type(text) is str and len(text) <= 64 and _SPELLING.fullmatch(text)):
        raise SeriesValueError('INVALID_MEASUREMENT_DECIMAL')
    try:
        value = Decimal(text)
    except InvalidOperation as error:  # pragma: no cover - the pattern admits only finite spellings
        raise SeriesValueError('INVALID_MEASUREMENT_DECIMAL') from error
    if not (value.is_finite() and abs(value.as_tuple().exponent) <= 308):
        raise SeriesValueError('MEASUREMENT_DECIMAL_LIMIT')
    return value


def decimal_text(value):
    return format(value, 'f')


def micro_value(value):
    """value * 10**6 as an int if exact and within BIGINT, else None (integer arithmetic only)."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise SeriesValueError('INVALID_MEASUREMENT_DECIMAL')
    n = int(''.join(map(str, digits))) if digits else 0
    shift = exponent + 6
    if shift >= 0:
        if n and shift > 19:  # n * 10**20 already exceeds BIGINT; never build a huge integer
            return None
        result = n * 10 ** shift
    else:
        result, remainder = divmod(n, 10 ** -shift)  # |exponent| <= 308 by decimal_of
        if remainder:
            return None
    result = -result if sign else result
    return result if BIGINT_MIN <= result <= BIGINT_MAX else None


def columns(text):
    """(decimal text, micro-unit integer or None) of an exported spelling."""
    value = decimal_of(text)
    return decimal_text(value), micro_value(value)
