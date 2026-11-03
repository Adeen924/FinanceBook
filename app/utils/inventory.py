"""
Inventory costing — FIFO and moving weighted average. Pure, no DB/UI.

A move is {date, qty (+in / −out), unit_cost (for ins), kind, id}. Ins with no
unit_cost (e.g. a customer return) come back at the current average/oldest
cost. Outs are costed by the method; their cost is Cost of Goods Sold.

Selling more than is on hand is allowed (records can lag reality): the excess
is costed at the last known unit cost and flagged, so totals never go wrong
silently.
"""
from decimal import Decimal

from utils.money import D, cents, ZERO

METHODS = {"fifo": "FIFO (first in, first out)", "avg": "Weighted average"}


def _sorted(moves):
    return sorted(moves, key=lambda m: (m.get("date") or "", m.get("created_at") or "",
                                        0 if D(m.get("qty")) > 0 else 1))


def run_costing(moves, method: str = "fifo", as_of: str = "") -> dict:
    """
    Process moves in date order (ins before outs on the same day).
    Returns {qty, value, unit_cost, cogs_by_move: {move_id: Decimal},
             costed: [move + {cost, qty_after, value_after}], short: bool}
    """
    layers: list[list[Decimal]] = []     # FIFO: [qty, unit_cost]
    qty = ZERO
    value = ZERO
    last_cost = ZERO
    short = False
    cogs: dict = {}
    costed = []
    for m in _sorted(moves):
        if as_of and (m.get("date") or "") > as_of:
            continue
        q = D(m.get("qty"))
        if q == 0:
            continue
        if q > 0:
            uc = D(m.get("unit_cost")) if m.get("unit_cost") not in (None, "") else None
            if uc is None or (uc == 0 and m.get("kind") in ("return_in",)):
                uc = (value / qty) if (method == "avg" and qty > 0) else (
                    layers[0][1] if layers else last_cost)
            layers.append([q, uc])
            qty += q
            value += q * uc
            last_cost = uc
            cost = cents(q * uc)
        else:
            out = -q
            if method == "avg":
                avg = (value / qty) if qty > 0 else last_cost
                if out > qty:
                    short = True
                cost = out * avg
                qty -= out
                value -= cost
                if qty <= 0:
                    value = ZERO if qty == 0 else qty * avg
            else:
                cost = ZERO
                remaining = out
                while remaining > 0 and layers:
                    take = min(remaining, layers[0][0])
                    cost += take * layers[0][1]
                    layers[0][0] -= take
                    remaining -= take
                    if layers[0][0] <= 0:
                        last_cost = layers[0][1]
                        layers.pop(0)
                if remaining > 0:
                    short = True
                    cost += remaining * last_cost
                qty -= out
                value = sum((l[0] * l[1] for l in layers), ZERO)
                if qty < 0:
                    value = qty * last_cost
            cost = cents(cost)
            cogs[m.get("id")] = cost
            cost = -cost
        costed.append(dict(m, cost=cost, qty_after=qty, value_after=cents(value)))
    return {"qty": qty, "value": cents(value),
            "unit_cost": cents(value / qty) if qty > 0 else cents(last_cost),
            "cogs_by_move": cogs, "costed": costed, "short": short}


def cogs_between(moves, method: str, start: str = "", end: str = "",
                 kinds=("sale", "adjust", "return_out")) -> Decimal:
    """Cost of goods sold (and shrinkage) for out-moves dated in [start, end]."""
    res = run_costing(moves, method, end)
    by_id = {m.get("id"): m for m in moves}
    total = ZERO
    for mid, cost in res["cogs_by_move"].items():
        m = by_id.get(mid, {})
        d = m.get("date") or ""
        if (not start or d >= start) and (not end or d <= end) and m.get("kind") in kinds:
            total += cost
    return cents(total)


def opening_value(moves, method: str, as_of: str = "") -> Decimal:
    """Value of 'opening' stock moves (stock you already had — no purchase entry)."""
    total = ZERO
    for m in moves:
        if m.get("kind") == "opening" and (not as_of or (m.get("date") or "") <= as_of):
            total += D(m.get("qty")) * D(m.get("unit_cost"))
    return cents(total)


def needs_reorder(on_hand, reorder_point) -> bool:
    rp = D(reorder_point)
    return rp > 0 and D(on_hand) <= rp
