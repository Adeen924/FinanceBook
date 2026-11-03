"""
Turn reviewed receipt line items into transaction split lines. Pure — no DB.

Each item: {description, line_total, category_id, class_id, charged_to,
            business (bool), kind ('expense' | 'asset' | 'inventory')}

Receipt-level amounts: tax, tip, shipping, duties (add to the total) and
discount (a positive number that reduces it).

plan_receipt_lines() returns (business_lines, personal_lines), each a list of
{category_id, class_id, charged_to, kind, amount (Decimal > 0 = money out),
 memo, item_indexes}. Lines are grouped by category/class/charged-to/kind so
one receipt books as one transaction split by category.
"""
from decimal import Decimal

from utils.money import D, cents, allocate, money_sum, ZERO

TAX_PROPORTIONAL = "proportional"
TAX_SEPARATE = "separate"

# Charges that can be booked as their own lines: (key, memo).
CHARGES = (("tax", "Sales tax"), ("tip", "Tip"), ("shipping", "Shipping"),
           ("duties", "Import duties / fees"))
_CAPITALISED = ("asset", "inventory")


def item_extras(items, mode=TAX_PROPORTIONAL, **amounts):
    """
    Each item's share of the receipt-level amounts, and the parts booked on
    their own lines: (extras [Decimal per item], separate {charge key: Decimal}).

    The discount is always spread over the items (it lowers what they cost).
    Proportional mode spreads every charge over the items. In separate mode a
    charge's business share goes to its own line — except the share that
    belongs to fixed-asset and inventory lines, which stays on them so their
    cost is the landed cost (and isn't also expensed). Personal items always
    carry their own share so the personal part is complete.
    """
    weights = [i.get("line_total") for i in items]
    extras = [ZERO] * len(items)
    separate = {}
    if not items:
        for key, _ in CHARGES:
            if cents(amounts.get(key)):
                separate[key] = cents(amounts.get(key))
        return extras, separate
    disc = cents(amounts.get("discount"))
    if disc:
        for idx, s in enumerate(allocate(disc, weights)):
            extras[idx] -= s
    for key, _ in CHARGES:
        amt = cents(amounts.get(key))
        if not amt:
            continue
        shares = allocate(amt, weights)
        if mode != TAX_SEPARATE:
            for idx, s in enumerate(shares):
                extras[idx] += s
            continue
        own = ZERO
        for idx, i in enumerate(items):
            if i.get("business", True) and (i.get("kind") or "expense") not in _CAPITALISED:
                own += shares[idx]
            else:
                extras[idx] += shares[idx]
        if own:
            separate[key] = own
    return extras, separate


def landed_costs(items, mode=TAX_PROPORTIONAL, **amounts) -> list[Decimal]:
    """What each item costs in the books: its line total + its share of the extras."""
    extras, _ = item_extras(items, mode, **amounts)
    return [cents(D(i.get("line_total")) + e) for i, e in zip(items, extras)]


def plan_receipt_lines(items, tax=0, tip=0, mode=TAX_PROPORTIONAL,
                       tax_category_id="", tip_category_id="", shipping=0, discount=0,
                       duties=0, shipping_category_id="", duties_category_id=""):
    items = [i for i in items if cents(i.get("line_total")) != 0]
    extras, separate = item_extras(items, mode, tax=tax, tip=tip, shipping=shipping,
                                   duties=duties, discount=discount)
    cats = {"tax": tax_category_id, "tip": tip_category_id,
            "shipping": shipping_category_id, "duties": duties_category_id}

    groups: dict = {}
    order: list = []
    for idx, i in enumerate(items):
        biz = bool(i.get("business", True))
        key = (biz, i.get("category_id") or "", i.get("class_id") or "",
               i.get("charged_to") or "", i.get("kind") or "expense")
        if key not in groups:
            groups[key] = {"category_id": key[1], "class_id": key[2], "charged_to": key[3],
                           "kind": key[4], "amount": ZERO, "descs": [], "item_indexes": []}
            order.append(key)
        g = groups[key]
        g["amount"] += cents(i.get("line_total")) + extras[idx]
        g["descs"].append(str(i.get("description") or "").strip())
        g["item_indexes"].append(idx)

    business, personal = [], []
    for key in order:
        g = groups.pop(key)
        descs = [d for d in g.pop("descs") if d]
        memo = ", ".join(descs)
        g["memo"] = memo if len(memo) <= 120 else memo[:117] + "…"
        g["amount"] = cents(g["amount"])
        (business if key[0] else personal).append(g)
    for key, memo in CHARGES:
        if separate.get(key):
            business.append({"category_id": cats[key], "amount": separate[key], "memo": memo,
                             "class_id": "", "charged_to": "", "kind": "expense",
                             "item_indexes": []})
    return business, personal


def receipt_total(items, tax=0, tip=0, shipping=0, discount=0, duties=0) -> Decimal:
    return cents(money_sum(i.get("line_total") for i in items) + D(tax) + D(tip)
                 + D(shipping) + D(duties) - D(discount))
