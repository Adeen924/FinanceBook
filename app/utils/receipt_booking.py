"""
Turn reviewed receipt line items into transaction split lines. Pure — no DB.

Each item: {description, line_total, category_id, class_id, charged_to,
            business (bool), kind ('expense' | 'asset' | 'inventory')}

plan_receipt_lines() returns (business_lines, personal_lines), each a list of
{category_id, class_id, charged_to, kind, amount (Decimal > 0 = money out),
 memo, item_indexes}. Lines are grouped by category/class/charged-to/kind so
one receipt books as one transaction split by category.
"""
from decimal import Decimal

from utils.money import D, cents, allocate, money_sum, ZERO

TAX_PROPORTIONAL = "proportional"
TAX_SEPARATE = "separate"


def plan_receipt_lines(items, tax=0, tip=0, mode=TAX_PROPORTIONAL,
                       tax_category_id="", tip_category_id=""):
    items = [i for i in items if cents(i.get("line_total")) != 0]
    tax, tip = cents(tax), cents(tip)
    extras = [ZERO] * len(items)
    separate = []
    if mode == TAX_PROPORTIONAL and items and (tax or tip):
        extras = allocate(tax + tip, [i.get("line_total") for i in items])
    elif mode == TAX_SEPARATE:
        # Personal items carry their own share of tax/tip so the personal part
        # is complete; only the business share becomes separate lines.
        shares = allocate(tax + tip, [i.get("line_total") for i in items]) if items else []
        biz_share = ZERO
        for idx, i in enumerate(items):
            if i.get("business", True):
                biz_share += shares[idx] if shares else ZERO
            else:
                extras[idx] = shares[idx]
        if tax + tip:
            tax_part = cents(biz_share * tax / (tax + tip)) if (tax + tip) else ZERO
            tip_part = biz_share - tax_part
            if tax_part:
                separate.append({"category_id": tax_category_id, "amount": tax_part,
                                 "memo": "Sales tax"})
            if tip_part:
                separate.append({"category_id": tip_category_id, "amount": tip_part,
                                 "memo": "Tip"})

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
    for s in separate:
        business.append(dict(s, class_id="", charged_to="", kind="expense", item_indexes=[]))
    return business, personal


def receipt_total(items, tax=0, tip=0) -> Decimal:
    return cents(money_sum(i.get("line_total") for i in items) + D(tax) + D(tip))
