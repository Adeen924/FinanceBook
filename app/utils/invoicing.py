"""
Invoice / bill math and AR/AP aging. Pure — no DB/UI.

FinanceBook books are CASH BASIS: an invoice or bill reaches the P&L only when
it is paid (the payment is a normal transaction). Open invoices/bills are
tracked here for aging reports and shown as memo lines, not in the P&L.
"""
from datetime import date

from utils.money import D, cents, allocate, money_sum, ZERO

AGING_BUCKETS = ["Current", "1–30 days", "31–60 days", "61–90 days", "Over 90 days"]


def line_amount(line: dict):
    return cents(D(line.get("qty") or 1) * D(line.get("unit_price")))


def line_tax(line: dict):
    return cents(line_amount(line) * D(line.get("tax_rate")) / 100)


def doc_totals(lines) -> dict:
    """{subtotal, tax, total} — tax is rounded per line, like most invoicing tools."""
    sub = money_sum(line_amount(l) for l in lines)
    tax = money_sum(line_tax(l) for l in lines)
    return {"subtotal": sub, "tax": tax, "total": cents(sub + tax)}


def balance_due(total, payments) -> object:
    return cents(D(total) - money_sum(p.get("amount") for p in payments))


def status_for(doc: dict, total, paid, as_of: str) -> str:
    """draft / void stay as set; otherwise paid, partial, overdue or open."""
    st = doc.get("status") or "open"
    if st in ("draft", "void"):
        return st
    due = cents(D(total) - D(paid))
    if due <= 0 and D(total) > 0:
        return "paid"
    if doc.get("due_date") and doc["due_date"] < as_of:
        return "overdue"
    if D(paid) > 0:
        return "partial"
    return "sent" if st == "sent" else "open"


def days_past_due(due_date: str, as_of: str) -> int:
    if not due_date:
        return 0
    return (date.fromisoformat(as_of) - date.fromisoformat(due_date)).days


def bucket_for(days: int) -> int:
    if days <= 0:
        return 0
    if days <= 30:
        return 1
    if days <= 60:
        return 2
    if days <= 90:
        return 3
    return 4


def aging(docs, as_of: str) -> dict:
    """
    docs: [{contact, due_date, balance}] (open balances only).
    Returns {"rows": {contact: [b0..b4, total]}, "totals": [b0..b4, total]}.
    """
    rows: dict = {}
    totals = [ZERO] * 6
    for d in docs:
        bal = cents(d.get("balance"))
        if bal <= 0:
            continue
        i = bucket_for(days_past_due(d.get("due_date", ""), as_of))
        row = rows.setdefault(d.get("contact") or "(no contact)", [ZERO] * 6)
        row[i] += bal
        row[5] += bal
        totals[i] += bal
        totals[5] += bal
    return {"rows": rows, "totals": totals}


def payment_split(lines, amount, total) -> list[dict]:
    """
    Split a (possibly partial) payment across a document's lines, in
    proportion to each line's share of the total, plus tax. Returns
    [{category_id, class_id, amount, memo, is_tax}] summing exactly to amount.
    """
    amount = cents(amount)
    parts = []
    for l in lines:
        parts.append({"category_id": l.get("category_id") or "", "class_id": l.get("class_id") or "",
                      "memo": l.get("description") or "", "weight": line_amount(l), "is_tax": False})
        t = line_tax(l)
        if t:
            parts.append({"category_id": "", "class_id": "", "memo": "Sales tax",
                          "weight": t, "is_tax": True})
    if not parts:
        return []
    shares = allocate(amount, [p["weight"] for p in parts])
    out = []
    merged: dict = {}
    for p, s in zip(parts, shares):
        key = (p["is_tax"], p["category_id"], p["class_id"])
        if key in merged:
            merged[key]["amount"] += s
            if p["memo"] and p["memo"] not in merged[key]["memo"]:
                merged[key]["memo"] += ", " + p["memo"]
        else:
            merged[key] = {"category_id": p["category_id"], "class_id": p["class_id"],
                           "amount": s, "memo": p["memo"], "is_tax": p["is_tax"]}
            out.append(merged[key])
    return [o for o in out if o["amount"] != 0]


def due_date_for(issue_date: str, terms_days: int) -> str:
    d = date.fromisoformat(issue_date)
    return date.fromordinal(d.toordinal() + max(int(terms_days or 0), 0)).isoformat()
