"""
Parsers for specific vendors' receipt layouts, registered with
receipt.register_vendor_parser(). Each takes the text rebuilt by
parsers/pdf_text.py (or OCR, or a pasted/emailed copy) and returns the
standard result dict; receipt.parse_receipt_text() falls back to the generic
heuristic parser when none matches.

Shared rules: subtotal is the items only (never a "Subtotal" that already
includes shipping), discounts are positive, and every result must satisfy
items + shipping + duties + tax + tip − discount = total.
"""
import re
from decimal import Decimal

from parsers.receipt import (Summary, amounts_in, classify, finish, label_before,
                             parse_date_info, register_vendor_parser, start_result,
                             text_lines, ambiguous_date_note, GENERIC_LABELS)
from utils.money import D, cents, money_sum

_CELLS = re.compile(r"\s{3,}")
_MAX_DESC = 250


def _clean_desc(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" -–:;,")
    return s if len(s) <= _MAX_DESC else s[:_MAX_DESC - 1].rstrip() + "…"


def _scan_summary(lines, labels, summary: Summary, start: int = 0, stop: int | None = None):
    """Classify the label next to the last amount on each line in [start, stop)."""
    for ln in lines[start:stop]:
        found = amounts_in(ln)
        if not found:
            continue
        pos, _, amt = found[-1]
        field = classify(label_before(ln, pos), labels)
        if field and field != "ignore":
            summary.add(field, amt)


def _item(desc, qty, unit, total, sku="") -> dict:
    return {"description": _clean_desc(desc), "qty": D(qty), "unit_price": cents(unit),
            "line_total": cents(total), "sku": sku}


# ── Amazon (Order Details page printed to PDF, or the order e-mail) ──────────

_AMZ_LABELS = [
    ("ignore", re.compile(r"total\s+before\s+tax|refund", re.I)),
    ("subtotal", re.compile(r"item\(?s\)?\s*sub[\s-]?total|items?\s+subtotal", re.I)),
    ("shipping_free", re.compile(r"free\s+(shipping|delivery)|shipping\s+(discount|promotion)|"
                                 r"free\s+.*\bshipping", re.I)),
    ("duties", re.compile(r"import\s+fees?|customs", re.I)),
    ("shipping", re.compile(r"shipping|handling|delivery", re.I)),
    # Paid with a gift card / points: the purchase still cost the full amount.
    ("paid_other", re.compile(r"gift\s+card\s+(amount|balance)|reward\s+points|"
                              r"points\s+(applied|redeemed)|amazon\s+pay\s+balance", re.I)),
    ("discount", re.compile(r"coupon|promotion|discount|savings|subscribe\s*&\s*save", re.I)),
    ("tax", re.compile(r"\btax\b", re.I)),
    ("total_strong", re.compile(r"grand\s+total|order\s+total", re.I)),
    ("total", re.compile(r"^total\b", re.I)),
]
_AMZ_PAYMENT = re.compile(r"balance applied|••••|\*{4}|^amazon gift card$|^gift card$|"
                          r"\b(visa|mastercard|american express|amex|discover|debit)\b|"
                          r"ending in|view related", re.I)
_AMZ_STOP = re.compile(r"^(back to top|conditions of use|©|\(c\)|https?://|your account|"
                       r"need help|amazon\.com, inc)", re.I)
_AMZ_NOISE = re.compile(
    r"^(sold by|supplied by|condition|return or replace|return items|eligible|"
    r"buy it again|view your item|track package|write a product review|get product support|"
    r"leave seller feedback|ask product question|problem with order|share gift receipt|"
    r"archive order|cancel items?|gift options|customi[sz]ations?|view related|"
    r"payment method|ship to|shipping address|order summary|return window|"
    r"return eligible|eligible for (a )?(return|refund|replacement)|non-returnable|"
    r"not returnable|returns? (are|is) (not )?accepted|refund issued|replacement)\b", re.I)
_AMZ_STATUS = re.compile(
    r"^(arriving|delivered|shipped|not yet shipped|package was|out for delivery|"
    r"your package|refunded|returned|expected|now arriving|delivery estimate|"
    r"preparing for shipment|shipment \d|in transit|running late)\b", re.I)
_PRICE_LINE = re.compile(r"^-?\$\s?[\d,]+\.\d{2}$")


def _is_amazon(text: str) -> bool:
    return bool(re.search(r"amazon\.(com|ca|co\.uk|de)", text, re.I) and
                re.search(r"order\s+placed|order\s*#|grand\s+total|item\(s\)\s+subtotal",
                          text, re.I))


def parse_amazon(text: str, method: str = "text") -> dict:
    res = start_result(text, method)
    res["vendor"] = "Amazon"
    lines = text_lines(text)
    m = re.search(r"order\s+placed:?\s*([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})", text, re.I)
    if m:
        res["date"], res["date_note"] = parse_date_info(m.group(1))[0], ""
    m = re.search(r"order\s*(?:#|number|no\.?)\s*:?\s*(\d{3}-\d{7}-\d{7}|[0-9A-Z-]{8,})",
                  text, re.I)
    if m:
        res["order_number"] = m.group(1)

    first = next((i for i, ln in enumerate(lines)
                  if re.search(r"item\(?s\)?\s*sub[\s-]?total", ln, re.I)), None)
    grand = next((i for i, ln in enumerate(lines) if re.search(r"grand\s+total", ln, re.I)),
                 None)
    summary = Summary()
    if first is not None and grand is not None and grand >= first:
        _amazon_summary(lines[first:grand + 2], summary)
        # Order Details pages list the items after the summary; the older
        # invoice layout ("1 of: …") lists them before it.
        items = _amazon_items(lines[grand + 1:]) or _amazon_items(lines[:first])
    else:
        _scan_summary(lines, _AMZ_LABELS, summary)
        items = _amazon_items(lines)
    _amazon_fix_qty(items, summary)
    paid_other = abs(summary.found.pop("paid_other", Decimal(0)))
    if paid_other:
        for key in ("total_strong", "total"):
            if key in summary.found:
                summary.found[key] += paid_other
        res["reader_notes"].append(
            f"${paid_other:,.2f} of this order was paid with an Amazon gift card or rewards, "
            "so the total includes it. Under Paid from, choose where that gift card came from "
            "(for example Due to Owner if you bought it yourself).")
    return finish(res, summary, items)


def _amazon_summary(block, summary: Summary):
    """
    Pair each summary label with its amount. Normally they share a line;
    when a layout pushes the amounts onto other lines (labels and amounts in
    separate columns) the labels and amounts are paired in order instead.
    """
    labels, amounts, pairs = [], [], []
    for ln in block:
        cells = [c.strip() for c in _CELLS.split(ln) if c.strip()
                 and not _AMZ_PAYMENT.search(c.strip())]
        line_labels = [classify(c, _AMZ_LABELS) for c in cells if not amounts_in(c)
                       and classify(c, _AMZ_LABELS)]
        line_amounts = [a for c in cells for _, _, a in amounts_in(c)]
        labels += line_labels
        amounts += line_amounts
        if len(line_labels) == 1 and len(line_amounts) == 1:
            pairs.append((line_labels[0], line_amounts[0]))
    if len(labels) == len(amounts) and len(pairs) < len(labels):
        pairs = list(zip(labels, amounts))
    for field, amt in pairs:
        if field != "ignore":
            summary.add(field, amt)


def _amazon_items(lines) -> list[dict]:
    items, title, qty = [], [], None
    # After "Sold by:" the title is complete; later lines (return window, …)
    # are details. They only become a new title if another "Sold by:" follows
    # before a price, i.e. the previous item had no price line.
    title_closed = False
    pending: list = []
    for ln in lines:
        s = _CELLS.split(ln.strip())[0].strip()
        if _AMZ_STOP.match(s):
            break
        m = re.match(r"^(\d+)\s+of:\s*(.*)$", s, re.I)           # older invoice layout
        if m:
            title, qty, title_closed = [m.group(2)], int(m.group(1)), False
            amts = amounts_in(ln)
            if amts and amts[-1][0] > len(ln) // 2:
                pos = amts[-1][0]
                title = [label_before(ln, pos) or m.group(2)]
                title[0] = re.sub(r"^\d+\s+of:\s*", "", title[0], flags=re.I)
                items.append(_amazon_item(title, qty, amts[-1][2]))
                title, qty = [], None
            continue
        mq = re.match(r"^(?:qty|quantity)\s*:?\s*(\d+)\b", s, re.I)
        if mq:
            qty = int(mq.group(1))
            continue
        if _PRICE_LINE.match(s):
            if title:
                items.append(_amazon_item(title, qty or 1, amounts_in(s)[-1][2]))
            title, qty, title_closed, pending = [], None, False, []
            continue
        if _AMZ_STATUS.match(s):
            title, qty, title_closed, pending = [], None, False, []
            continue
        if _AMZ_NOISE.match(s):
            if title_closed and pending and re.match(r"^sold by", s, re.I):
                title, pending = pending, []           # a new item began
            elif title:
                title_closed = True
            continue
        if title_closed:
            pending.append(s)
            continue
        title.append(s)
    return items


def _amazon_item(title, qty, price) -> dict:
    qty = D(qty) or Decimal(1)
    return _item(" ".join(title), qty, price, cents(price * qty))


def _amazon_fix_qty(items, summary: Summary):
    # Amazon prints the unit price beside each item; if the subtotal says the
    # printed prices were already line totals, use them as such.
    sub = summary.found.get("subtotal")
    if not sub or not any(i["qty"] != 1 for i in items):
        return
    if money_sum(i["line_total"] for i in items) != sub and \
            money_sum(i["unit_price"] for i in items) == sub:
        for i in items:
            i["line_total"] = i["unit_price"]
            i["unit_price"] = cents(i["line_total"] / i["qty"])


register_vendor_parser("amazon", _is_amazon, parse_amazon)


# ── DigiKey invoice ──────────────────────────────────────────────────────────

_DK_LABELS = [
    ("ignore", re.compile(r"charges\s+subtotal|subtotal", re.I)),
    ("subtotal", re.compile(r"sales\s+amount|merchandise\s+total", re.I)),
    ("duties", re.compile(r"tariff|dut(y|ies)|customs|import", re.I)),
    ("shipping", re.compile(r"shipping|freight|handling", re.I)),
    ("total_strong", re.compile(r"total\s+charged|invoice\s+total|amount\s+due|total\s+due",
                                re.I)),
    ("discount", re.compile(r"discount|promotion|coupon|credit", re.I)),
    ("tax", re.compile(r"\btax\b|\bvat\b|\bgst\b", re.I)),
    ("total", re.compile(r"\btotal\b", re.I)),
]
_DK_ROW = re.compile(
    r"^\s*(?P<line>\d{1,3})\s+(?P<ord>\d+)\s+(?P<canc>\d+)\s+(?P<ship>\d+)\s+"
    r"PART:\s*(?P<part>\S+)(?P<mid>.*?)\s+(?P<unit>\d[\d,]*\.\d{2,5})\s+"
    r"(?P<amt>-?[\d,]+\.\d{2})(?:\s+T)?\s*$")


def _is_digikey(text: str) -> bool:
    return bool(re.search(r"digi-?key", text, re.I) and
                re.search(r"invoice\s*#|sales\s+amount|salesorder", text, re.I))


def parse_digikey(text: str, method: str = "text") -> dict:
    res = start_result(text, method)
    res["vendor"] = "DigiKey"
    lines = text_lines(text)
    m = re.search(r"invoice\s*#\s*:?\s*(\d{6,})", text, re.I)
    if m:
        res["order_number"] = m.group(1)
    res["date"] = _digikey_date(lines) or res["date"]
    res["date_note"] = ""
    items = []
    for ln in lines:
        m = _DK_ROW.match(ln)
        if not m:
            continue
        desc = re.search(r"DESC:\s*(.+)$", m.group("mid").strip())
        qty = int(m.group("ship")) or int(m.group("ord")) - int(m.group("canc"))
        unit, amt = D(m.group("unit").replace(",", "")), D(m.group("amt").replace(",", ""))
        if qty and cents(qty * unit) != cents(amt) and int(m.group("ord")):
            qty = int(m.group("ord"))
        items.append(_item(desc.group(1) if desc else m.group("part"), qty or 1, unit, amt,
                           sku=m.group("part")))
    if not items:
        items = _digikey_items_loose(lines)
    summary = Summary()
    start = next((i for i, ln in enumerate(lines) if re.search(r"sales\s+amount", ln, re.I)), 0)
    _scan_summary(lines, _DK_LABELS, summary, start)
    return finish(res, summary, items)


def _digikey_date(lines) -> str:
    """Invoice Date (else Order Date) from the header row of dates."""
    for i, ln in enumerate(lines[:-1]):
        if not re.search(r"invoice\s+date", ln, re.I):
            continue
        heads = [c.strip().lower() for c in _CELLS.split(ln.strip())]
        vals = [c.strip() for c in _CELLS.split(lines[i + 1].strip())]
        if len(heads) == len(vals):
            for want in ("invoice date", "order date"):
                for h, v in zip(heads, vals):
                    if h.startswith(want):
                        d = parse_date_info(v)[0]
                        if d:
                            return d
        return parse_date_info(lines[i + 1])[0]
    return ""


def _digikey_items_loose(lines) -> list[dict]:
    """Fallback when a row's columns aren't on one line: pair PART/DESC/amount lists."""
    parts = [m.group(1) for ln in lines for m in [re.search(r"PART:\s*(\S+)", ln)] if m]
    descs = [m.group(1).strip() for ln in lines
             for m in [re.search(r"DESC:\s*(.+?)(?:\s{3,}|$)", ln)] if m]
    rows = [m for ln in lines for m in
            [re.search(r"(?<![\d.])(\d[\d,]*\.\d{3,5})\s+(-?[\d,]+\.\d{2})\s*T?\s*$", ln)] if m]
    out = []
    for k, m in enumerate(rows):
        unit, amt = D(m.group(1).replace(",", "")), D(m.group(2).replace(",", ""))
        qty = (amt / unit).to_integral_value() if unit else Decimal(1)
        part = parts[k] if k < len(parts) else ""
        out.append(_item(descs[k] if k < len(descs) else part or "Item", qty or 1, unit, amt,
                         sku=part))
    return out


register_vendor_parser("digikey", _is_digikey, parse_digikey)


# ── JLCPCB invoice (an image: read by OCR or AI) ─────────────────────────────

_JLC_LABELS = [
    ("subtotal", re.compile(r"merchandise\s+total", re.I)),
    ("discount", re.compile(r"discount|coupon|promotion", re.I)),
    ("ignore", re.compile(r"^sub[\s-]?total", re.I)),
    ("duties", re.compile(r"import|customs|dut(y|ies)|tariff", re.I)),
    ("shipping", re.compile(r"shipping|freight", re.I)),
    ("tax", re.compile(r"\btax\b|\bvat\b|\bgst\b", re.I)),
    ("total_strong", re.compile(r"grand\s+total", re.I)),
    ("total", re.compile(r"\btotal\b", re.I)),
]


def _is_jlcpcb(text: str) -> bool:
    return bool(re.search(r"jlcpcb|jialichuang", text, re.I))


def parse_jlcpcb(text: str, method: str = "text") -> dict:
    res = start_result(text, method)
    res["vendor"] = "JLCPCB"
    lines = text_lines(text)
    m = re.search(r"invoice\s*no\.?\s*:?\s*([0-9A-Z]{3,}(?: [0-9A-Z]+)*)", text, re.I)
    if m and len(m.group(1).replace(" ", "")) >= 8:
        res["order_number"] = m.group(1).replace(" ", "")      # OCR may split it
    m = re.search(r"invoice\s*date\s*:?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})", text, re.I)
    if m:
        # JLCPCB (Hong Kong) prints day/month/year.
        res["date"] = parse_date_info(m.group(1), day_first=True)[0]
        _, raw = parse_date_info(m.group(1))
        res["date_note"] = (f"Read {raw} as day/month (JLCPCB's format) — check it."
                            if raw else "")
    items = []
    summary_from = len(lines)
    for i, ln in enumerate(lines):
        if re.search(r"merchandise\s+total", ln, re.I):
            summary_from = min(summary_from, i)
            break
        cells = [c.strip() for c in _CELLS.split(ln.strip()) if c.strip()]
        amts = amounts_in(ln)
        if len(cells) < 4 or len(amts) < 2 or not re.fullmatch(r"\d{1,3}", cells[0]):
            continue
        money_cells = [k for k, c in enumerate(cells) if amounts_in(c)]
        qty_cell = next((cells[k] for k in range(money_cells[0] - 1, 0, -1)
                         if re.fullmatch(r"\d+", cells[k])), "1")
        items.append(_item(cells[1], int(qty_cell), amts[-2][2], amts[-1][2]))
    summary = Summary()
    _scan_summary(lines, _JLC_LABELS, summary, summary_from)
    return finish(res, summary, items)


register_vendor_parser("jlcpcb", _is_jlcpcb, parse_jlcpcb)
