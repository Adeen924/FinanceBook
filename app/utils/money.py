"""
Cent-exact money helpers.

Amounts are stored as REAL in SQLite (see sheets/client.py) and passed around
as strings/floats, so every total must be summed and rounded through Decimal to
avoid float drift ("0.1 + 0.2"). Pure functions — no DB/UI imports.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(value) -> Decimal:
    """Any amount-like value (str/float/int/Decimal/None/'') → Decimal (unrounded)."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    if isinstance(value, float):
        # repr() gives the shortest string that round-trips, so 0.1 → "0.1".
        value = repr(value)
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip() or "0")
    except InvalidOperation:
        return ZERO


def cents(value) -> Decimal:
    """Round to whole cents, half away from zero (the usual bookkeeping rule)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money_sum(values) -> Decimal:
    """Exact sum of amounts, rounded to cents once at the end."""
    total = ZERO
    for v in values:
        total += D(v)
    return cents(total)


def to_float(value) -> float:
    """Cent-rounded float, for storing in the REAL columns / Qt widgets."""
    return float(cents(value))


def allocate(total, weights) -> list[Decimal]:
    """
    Split `total` across `weights` proportionally, in cents, so the parts always
    add back up to exactly `total` (the rounding remainder goes to the largest
    parts first). Used for spreading receipt tax/tip over line items.
    """
    total = cents(total)
    ws = [abs(D(w)) for w in weights]
    if not ws:
        return []
    wsum = sum(ws)
    if wsum == 0:
        ws = [Decimal(1)] * len(ws)
        wsum = Decimal(len(ws))
    raw = [total * w / wsum for w in ws]
    parts = [r.quantize(CENT, rounding=ROUND_HALF_UP) for r in raw]
    diff = total - sum(parts)
    step = CENT if diff > 0 else -CENT
    order = sorted(range(len(ws)), key=lambda i: -ws[i])
    i = 0
    while diff != 0 and order:
        parts[order[i % len(order)]] += step
        diff -= step
        i += 1
    return parts


def fmt(value, parens: bool = True) -> str:
    """$1,234.56 / ($1,234.56) display string."""
    v = cents(value)
    if v < 0:
        return f"(${abs(v):,.2f})" if parens else f"-${abs(v):,.2f}"
    return f"${v:,.2f}"
