"""
Receipt parsing — pluggable, stdlib-only (plus `requests` for the optional AI
reader). No Qt imports, so it is unit-testable.

Every parser returns the same dict shape (see empty_result()):

    {vendor, date (YYYY-MM-DD or ''), currency, subtotal, shipping, discount,
     duties, tax, tip, total, order_number,
     line_items: [{description, qty, unit_price, line_total, sku}],
     method, raw_text, date_note, warnings: [str]}

subtotal is the items only. The books balance as
    items + shipping + duties + tax + tip − discount = total
(discount is a positive number). Money values are Decimal (cents).

Order of preference, as wired up in parse_file():
  1. text-based receipts (.txt .eml .html .htm, PDFs with a text layer — see
     parsers/pdf_text.py) → vendor parsers (receipt_vendors.py), else the
     heuristic text parser
  2. images and image-only PDFs → offline Windows OCR (parsers/ocr.py), then
     the same text parsers
  3. optional AI reader (Anthropic Messages API, only when the caller passes an
     API key — the UI makes this opt-in)
  4. anything else → empty result for manual entry
Register extra file parsers with register_parser(), and parsers for one
vendor's layout with register_vendor_parser().
"""
import base64
import html
import json
import re
import zlib
from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser

from utils.money import D, cents, money_sum, ZERO

TEXT_EXTS = {".txt", ".text", ".eml", ".html", ".htm", ".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SUPPORTED_EXTS = TEXT_EXTS | IMAGE_EXTS
_IMAGE_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}
OCR_EXTS = {".jpg", ".jpeg", ".png", ".gif"}      # what the Windows decoder always reads

MONEY_FIELDS = ("subtotal", "shipping", "discount", "duties", "tax", "tip", "total")
MSG_IMAGE = "This is an image. Use Read with AI, or type it in."
MSG_BAD_PDF = ("This PDF couldn't be read (it may be encrypted or damaged). "
               "Use Read with AI, or type it in.")
MSG_NO_TEXT_PDF = ("This PDF has no readable text (it may be drawn as shapes). "
                   "Use Read with AI, or type it in.")
MSG_NO_TOTALS = ("No totals were found in this file. Check the text on the left, then use "
                 "Read with AI or type the amounts in.")


def empty_result(method: str = "manual") -> dict:
    res = {"vendor": "", "date": "", "currency": "USD", "line_items": [],
           "order_number": "", "method": method, "raw_text": "", "date_note": "",
           "reader_notes": [], "warnings": []}
    for k in MONEY_FIELDS:
        res[k] = ZERO
    return res


# ── text extraction ──────────────────────────────────────────────────────────

class _TextFromHTML(HTMLParser):
    """HTML → text, keeping table rows and block elements on their own lines."""
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "section", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("  ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        out = "".join(self.parts).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in out.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def html_to_text(markup: str) -> str:
    p = _TextFromHTML()
    try:
        p.feed(markup)
        p.close()
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return p.text()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def eml_to_text(data: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    header = "\n".join(f"{k}: {msg[k]}" for k in ("From", "Subject", "Date") if msg[k])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            content = body.get_content()
        except Exception:
            content = _decode(body.get_payload(decode=True) or b"")
        text = html_to_text(content) if body.get_content_type() == "text/html" else content
    return (header + "\n\n" + text).strip()


_PDF_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
_PDF_TEXT_OP = re.compile(rb"\[(.*?)\]\s*TJ|\((.*?)(?<!\\)\)\s*Tj|(T\*|Td|TD|Tm|ET|')", re.S)
_TYPOGRAPHY = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
                             "ﬄ": "ffl", "’": "'", "‘": "'", "“": '"',
                             "”": '"', " ": " ", "−": "-", "–": "-"})


def _pdf_unescape(s: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):          # backslash
            n = s[i + 1]
            esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
            if n in esc:
                out.append(esc[n]); i += 2; continue
            if 0x30 <= n <= 0x37:
                m = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
                out.append(int(m.group(0), 8) & 0xFF); i += 1 + len(m.group(0)); continue
            out.append(n); i += 2; continue
        out.append(c)
        i += 1
    return out.decode("latin-1")


def _legacy_pdf_to_text(data: bytes) -> str:
    """The original zlib + regex reader, kept as a fallback for odd files."""
    chunks: list[str] = []
    for m in _PDF_STREAM.finditer(data):
        head, body = m.group(1), m.group(2)
        if b"/Subtype/Image" in head.replace(b" ", b"") or b"/Image" in head:
            continue
        if b"FlateDecode" in head:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                try:
                    body = zlib.decompressobj().decompress(body)
                except zlib.error:
                    continue
        if b"Tj" not in body and b"TJ" not in body:
            continue
        line: list[str] = []
        for op in _PDF_TEXT_OP.finditer(body):
            arr, single, brk = op.group(1), op.group(2), op.group(3)
            if brk:
                if line:
                    chunks.append("".join(line))
                    line = []
                continue
            if single is not None:
                line.append(_pdf_unescape(single))
            elif arr is not None:
                for part in re.finditer(rb"\((.*?)(?<!\\)\)|(-?\d+(?:\.\d+)?)", arr, re.S):
                    if part.group(1) is not None:
                        line.append(_pdf_unescape(part.group(1)))
                    elif part.group(2) and float(part.group(2)) < -200:
                        line.append(" ")           # large kerning gap = space
        if line:
            chunks.append("".join(line))
    text = "\n".join(c.strip() for c in chunks if c.strip())
    # Reject binary noise (encrypted/CID PDFs produce mostly non-printables).
    printable = sum(ch.isprintable() or ch == "\n" for ch in text)
    return text if text and printable / len(text) > 0.9 else ""


def read_pdf(data: bytes):
    """parsers.pdf_text.PdfContent for a PDF, or None if it can't be read at all."""
    from parsers import pdf_text
    try:
        return pdf_text.read_pdf(data)
    except Exception:
        return None


def pdf_to_text(data: bytes) -> str:
    """
    Text of a PDF's text layer, rebuilt into lines from glyph positions (see
    parsers/pdf_text.py: CID/ToUnicode and WinAnsi fonts, object streams…).
    '' for image-only, encrypted or broken files.
    """
    doc = read_pdf(data)
    text = doc.text if doc is not None else ""
    if not text.strip() and (doc is None or not doc.images):
        text = _legacy_pdf_to_text(data)
    return text.translate(_TYPOGRAPHY)


def extract_text(path: str) -> str:
    """Plain text of a receipt file, or '' when it has none we can read."""
    ext = _ext(path)
    with open(path, "rb") as f:
        data = f.read()
    if ext in (".txt", ".text"):
        return _decode(data)
    if ext in (".html", ".htm"):
        return html_to_text(_decode(data))
    if ext == ".eml":
        return eml_to_text(data)
    if ext == ".pdf":
        return pdf_to_text(data)
    return ""


def _ext(path: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]+$", path)
    return m.group(0).lower() if m else ""


# ── amounts, dates ───────────────────────────────────────────────────────────

# Kept for callers that only need "is there an amount on this line".
_AMOUNT = r"-?\(?\$?\s?-?\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?|-?\(?\$?\s?-?\d+\.\d{2}\)?"
_AMOUNT_RE = re.compile(_AMOUNT)
# A money amount: exactly two decimals, not part of a longer number
# (8471.50.0150, 8.42000), not a percentage.
_MONEY_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![\d])(?!\.\d)(?!\s?%)")
_NEG_BEFORE = re.compile(r"(?:^|[\s:(])-\s*(?:[A-Z]{3}\s*)?[$€£]?\s*$|\(\s*(?:[A-Z]{3}\s*)?[$€£]?\s*$")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def amounts_in(line: str) -> list[tuple[int, int, Decimal]]:
    """[(start, end, Decimal)] for each money amount on a line; '-$5' / '($5)' are negative."""
    out = []
    for m in _MONEY_RE.finditer(line):
        before = line[max(0, m.start() - 8):m.start()]
        v = D(m.group(1).replace(",", "") + "." + m.group(2))
        if _NEG_BEFORE.search(before):
            v = -v
        out.append((m.start(), m.end(), cents(v)))
    return out


def _money(token: str) -> Decimal:
    neg = token.strip().startswith("-") or ("(" in token and ")" in token)
    v = D(re.sub(r"[^\d.]", "", token))
    return cents(-v if neg else v)


_DATE_PATS = [
    (re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2}|\d{2})\b"), "num"),
    (re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})"
                r"(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I), "bdy"),
    (re.compile(r"\b(\d{1,2})[\s-](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
                r"\.?[\s,-]+(20\d{2})\b", re.I), "dby"),
]


def parse_date_info(text: str, day_first: bool | None = None) -> tuple[str, str]:
    """
    (first plausible date as YYYY-MM-DD or '', the raw text if it was an
    ambiguous numeric date like 08/10/2026 read without a day_first hint).
    Numeric dates are month/day unless day_first, or unless the first number
    can't be a month (31/12/2026).
    """
    best = None
    for rx, kind in _DATE_PATS:
        for m in rx.finditer(text):
            ambiguous = ""
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "num":
                    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    y += 2000 if y < 100 else 0
                    if a > 12 >= b:
                        d, mo = a, b
                    elif b > 12 >= a:
                        mo, d = a, b
                    else:
                        mo, d = (b, a) if day_first else (a, b)
                        if a != b and day_first is None:
                            ambiguous = m.group(0)
                elif kind == "bdy":
                    mo, d, y = _MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
                else:
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2)[:3].lower()], int(m.group(3))
                found = date(y, mo, d)
            except (ValueError, KeyError):
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), found, ambiguous)
    return (best[1].isoformat(), best[2]) if best else ("", "")


def parse_date(text: str, day_first: bool | None = None) -> str:
    """First plausible date in the text as YYYY-MM-DD, or ''."""
    return parse_date_info(text, day_first)[0]


def ambiguous_date_note(raw: str) -> str:
    return f"Date is ambiguous ({raw}) — check it."


_DATE_LABEL = re.compile(r"\b(order\s+placed|order\s+date|invoice\s+date|date\s+of\s+purchase|"
                         r"purchase\s+date|transaction\s+date|receipt\s+date|date)\b", re.I)
# Chrome/Edge print header ("11/3/26, 10:33 AM   Order Details") — the print date.
_PRINT_HEADER = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4},\s+\d{1,2}:\d{2}\s*[AP]M\b", re.I)


def find_date(lines: list[str], day_first: bool | None = None) -> tuple[str, str]:
    """Prefer a date on a labelled line; skip browser print headers and URLs."""
    usable = [ln for ln in lines if not _PRINT_HEADER.match(ln)
              and not re.match(r"^\s*https?://", ln)]
    for i, ln in enumerate(usable):
        if _DATE_LABEL.search(ln):
            for probe in (ln, " ".join(usable[i:i + 2])):
                found = parse_date_info(probe[_DATE_LABEL.search(probe).start():], day_first)
                if found[0]:
                    return found
    return parse_date_info("\n".join(usable), day_first)


# ── summary lines (subtotal / shipping / tax / … / total) ────────────────────

# (field, regex) — checked in this order against the label next to an amount;
# the first match decides. "subtotal" = items only; "subtotal_after" = a bare
# "Subtotal" that may already include shipping; "ignore" = cross-check lines.
GENERIC_LABELS = [
    ("ignore", re.compile(r"before\s+tax|pre-?tax|taxable\s+(amount|subtotal)|tax\s*rate|"
                          r"\bsavings\b|you\s+saved|refund|charges?\s+subtotal|"
                          r"ship(ping)?\s+(to|address|method|via)|bill(ing)?\s+(to|address)",
                          re.I)),
    ("subtotal", re.compile(r"merchandise\s+(sub)?total|item\(?s\)?\s*sub[\s-]?total|"
                            r"items?\s+(sub)?total|sales\s+amount|product\s+total", re.I)),
    ("shipping_free", re.compile(r"free\s+(shipping|delivery)|shipping\s+(discount|promotion)",
                                 re.I)),
    ("duties", re.compile(r"import\s+(tax(es)?|dut(y|ies)|fees?|charges?)|customs|\bdut(y|ies)\b|"
                          r"tariff", re.I)),
    ("shipping", re.compile(r"shipping|handling|delivery\s+(fee|charge)|\bdelivery\b|freight|"
                            r"postage|\bs\s*&\s*h\b", re.I)),
    ("discount", re.compile(r"discount|promotion|\bpromo\b|coupon|gift\s+card|credit\s+applied",
                            re.I)),
    ("tip", re.compile(r"\btip\b|gratuity", re.I)),
    ("tax", re.compile(r"\btax(es)?\b|\bvat\b|\bgst\b|\bhst\b|\bpst\b", re.I)),
    ("subtotal_after", re.compile(r"\bsub[\s-]?total\b", re.I)),
    ("total_strong", re.compile(r"grand\s+total|total\s+charged|amount\s+(due|paid|charged)|"
                                r"order\s+total|invoice\s+total|balance\s+due|total\s+(due|paid)",
                                re.I)),
    ("total", re.compile(r"\btotal\b", re.I)),
]

_CELL_SPLIT = re.compile(r"\s{3,}")


def label_before(line: str, start: int) -> str:
    """The text cell just left of an amount (layout text uses 3+ spaces between cells)."""
    left = line[:start]
    left = re.sub(r"(?:-\s*)?(?:[A-Z]{3}\s*)?[$€£(]?\s*$", "", left).rstrip()
    cells = [c for c in _CELL_SPLIT.split(left) if c.strip()]
    return cells[-1].strip() if cells else ""


def classify(label: str, labels=GENERIC_LABELS) -> str:
    return next((f for f, rx in labels if rx.search(label)), "")


class Summary:
    """Collects summary amounts as they are found; shipping & discounts accumulate."""

    def __init__(self):
        self.found: dict[str, Decimal] = {}

    def add(self, field: str, amt: Decimal):
        f = self.found
        if field == "shipping_free":
            f["shipping"] = f.get("shipping", ZERO) - abs(amt)
        elif field == "shipping":
            f["shipping"] = f.get("shipping", ZERO) + amt
        elif field == "discount":
            f["discount"] = f.get("discount", ZERO) + abs(amt)
        elif field == "duties":
            f["duties"] = f.get("duties", ZERO) + amt
        elif field in ("total", "total_strong"):
            # "Total" can repeat (e.g. per shipment); keep the largest of the
            # strongest kind of label.
            key = field
            f[key] = max(f[key], amt) if key in f else amt
        else:
            f.setdefault(field, amt)

    def __contains__(self, k):
        return k in self.found


def finish(res: dict, summary: Summary, items: list[dict]) -> dict:
    """
    Fill the money fields from what was found. subtotal is always the items
    only: a labelled items subtotal wins, then a bare "Subtotal" (less
    shipping/discount when the totals show it includes them), then the sum
    of the items.
    """
    f = summary.found
    for k in ("shipping", "discount", "duties", "tax", "tip"):
        res[k] = cents(f.get(k, ZERO))
    res["line_items"] = annotate_packs(items)
    total = f.get("total_strong", f.get("total", ZERO))
    extras = res["shipping"] + res["duties"] + res["tax"] + res["tip"] - res["discount"]
    if "subtotal" in f:
        sub = f["subtotal"]
    elif "subtotal_after" in f:
        sub = f["subtotal_after"]
        net_ship = res["shipping"] - res["discount"]
        if net_ship and total and cents(sub + res["duties"] + res["tax"] + res["tip"]) == total:
            sub -= net_ship
    elif items:
        sub = money_sum(i["line_total"] for i in items)
    else:
        sub = ZERO
    res["subtotal"] = cents(sub)
    res["total"] = cents(total) if total else cents(res["subtotal"] + extras)
    res["warnings"] = check_totals(res)
    return res


# ── heuristic text parser ────────────────────────────────────────────────────

_SKIP_LINE = re.compile(
    r"\b(change|cash tendered|tendered|visa|mastercard|amex|discover|debit|credit|"
    r"card\s*#|auth|approval|balance|savings|you saved|points|rewards|"
    r"payment|paid with|refund due|shipping address|ship to|bill to|page \d+ of)\b", re.I)
_QTY_PATTERNS = [
    re.compile(r"^(?P<desc>.*?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?:x|@|×)\s*\$?(?P<unit>\d+(?:\.\d{1,2})?)\b", re.I),
    re.compile(r"^(?P<qty>\d+(?:\.\d+)?)\s*(?:x|@|×)\s*(?P<desc>.+?)\s+@?\s*\$?(?P<unit>\d+\.\d{2})\b", re.I),
    re.compile(r"\bqty[:\s]*(?P<qty>\d+(?:\.\d+)?)\b", re.I),
    re.compile(r"^(?P<qty>\d{1,3})\s+(?P<desc>[A-Za-z].+)$"),
]


def _guess_vendor(lines: list[str]) -> str:
    for ln in lines[:8]:
        if _PRINT_HEADER.match(ln):
            continue
        s = ln.strip(" *-=#")
        if len(s) < 2 or _AMOUNT_RE.search(s) or parse_date(s):
            continue
        if re.match(r"^(from|subject|date|to|receipt|invoice|order|thank)", s, re.I):
            m = re.match(r"^from:\s*\"?([^\"<]+)", s, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()
            continue
        if re.search(r"\b(st|street|ave|road|rd|suite|blvd)\b|\d{5}", s, re.I):
            continue
        if sum(ch.isalpha() for ch in s) >= 2:
            return _CELL_SPLIT.split(s)[0][:80]
    return ""


def _currency(text: str) -> str:
    if re.search(r"\bCAD\b|C\$", text):
        return "CAD"
    if re.search(r"€|\bEUR\b", text):
        return "EUR"
    if re.search(r"£|\bGBP\b", text):
        return "GBP"
    return "USD"


def text_lines(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in text.translate(_TYPOGRAPHY).replace("\r", "").split("\n")]
    return [ln for ln in lines if ln.strip()]


def start_result(text: str, method: str = "text", day_first: bool | None = None) -> dict:
    """A result with raw_text, vendor guess, date (+ ambiguity note) and currency."""
    res = empty_result(method)
    res["raw_text"] = text
    lines = text_lines(text)
    res["vendor"] = _guess_vendor(lines)
    res["date"], raw = find_date(lines, day_first)
    if raw:
        res["date_note"] = ambiguous_date_note(raw)
    res["currency"] = _currency(text)
    return res


def parse_text(text: str, method: str = "text") -> dict:
    """Heuristically read vendor/date/totals/line items from receipt text."""
    res = start_result(text, method)
    summary = Summary()
    items = []
    for ln in text_lines(text):
        found = amounts_in(ln)
        if not found:
            continue
        start, _, amt = found[-1]
        cell = label_before(ln, start)
        label = ln[:start].strip()
        field = classify(cell) or classify(label)
        if field == "discount" and not ({"subtotal", "subtotal_after"} & summary.found.keys()):
            field = ""          # a coupon among the items stays a negative line item
        if field:
            if field == "tax" and re.search(r"\brate\b", label, re.I) and amt < 1:
                continue        # "Tax rate 0.06", not an amount
            if field != "ignore":
                summary.add(field, amt)
            continue
        label = re.sub(r"[-$(\s]+$", "", label)
        if _SKIP_LINE.search(label) or not re.search(r"[A-Za-z]{2,}", label):
            continue
        items.append(_item_from_line(label, amt))
    return finish(res, summary, items)


def _item_from_line(label: str, amt: Decimal) -> dict:
    desc, qty, unit = label, Decimal(1), amt
    for pat in _QTY_PATTERNS:
        m = pat.search(label)
        if not m:
            continue
        gd = m.groupdict()
        try:
            qty = D(gd["qty"])
        except Exception:
            continue
        if qty <= 0:
            qty = Decimal(1)
        if gd.get("desc"):
            desc = gd["desc"]
        else:
            desc = pat.sub("", label)
        unit = D(gd["unit"]) if gd.get("unit") else cents(amt / qty)
        break
    desc = re.sub(r"\s{2,}", " ", desc).strip(" .:-$")
    return {"description": desc or label, "qty": qty, "unit_price": cents(unit),
            "line_total": amt, "sku": ""}


# ── pack sizes ("6 Pack" = 6 stock units per qty) ───────────────────────────

_MEASURE = (r"(?:\s*[x×]\s*\d|\s*(?:ft|feet|foot|in|inch|inches|mm|cm|m|meters?|oz|lbs?|g|kg|"
            r"gb|tb|ml|l|v|w|mah|gauge|awg)\b|\s*(?:''|\"|'|”|’))")
_PACK_PATTERNS = [
    re.compile(r"\b(\d{1,5})\s*-?\s*(?:pack|pk|pcs|pc|pieces|piece|sets)\b", re.I),
    re.compile(r"\bpack\s+of\s+(\d{1,4})\b(?!" + _MEASURE + ")", re.I),
    re.compile(r"\bset\s+of\s+(\d{1,4})\b(?!" + _MEASURE + ")", re.I),
    re.compile(r"\b(\d{1,4})\s*-?\s*(?:count|ct)\b", re.I),
]
# "3 Pack of 10 ft cables": 3 packs of a 10-count? or one 3-pack of 10 ft cables?
_PACK_OF_NUMBER = re.compile(r"\b\d{1,4}\s*-?\s*(?:pack|sets?)\s+of\s+\d+(?!\s*[x×]\s*\d)", re.I)


def suggest_pack_size(description: str) -> dict | None:
    """
    A suggested units-per-pack from an item title, or None. Returns
    {"units", "text" (what matched), "ambiguous"}; an ambiguous suggestion is
    shown to the user but never applied for them.
    """
    found = []
    for rx in _PACK_PATTERNS:
        for m in rx.finditer(description or ""):
            n = int(m.group(1))
            if n > 1:
                found.append((m.start(), n, m.group(0).strip()))
    if not found:
        return None
    found.sort()
    _, units, text = found[0]
    ambiguous = len({n for _, n, _ in found}) > 1 or bool(_PACK_OF_NUMBER.search(description))
    return {"units": units, "text": text, "ambiguous": ambiguous}


def annotate_packs(items: list[dict]) -> list[dict]:
    for i in items:
        i["pack_suggestion"] = suggest_pack_size(i.get("description", ""))
    return items


def check_totals(res: dict) -> list[str]:
    """Human-readable mismatches between items, subtotal and total."""
    warn = []
    items = res.get("line_items") or []
    item_sum = money_sum(i.get("line_total") for i in items)
    sub, ship, disc, duty, tax, tip, total = (cents(res.get(k)) for k in MONEY_FIELDS)
    if items and sub and item_sum != sub:
        warn.append(f"Line items add up to ${item_sum:,.2f} but the subtotal is ${sub:,.2f} "
                    f"(off by ${abs(item_sum - sub):,.2f}).")
    base = sub or item_sum
    calc = cents(base + ship + duty + tax + tip - disc)
    if total and base and calc != total:
        parts = ["items"] + [n for n, v in (("shipping", ship), ("duties", duty), ("tax", tax),
                                            ("tip", tip)) if v]
        expr = " + ".join(parts) + (" − discount" if disc else "")
        warn.append(f"{expr[0].upper() + expr[1:]} = ${calc:,.2f} but the total is "
                    f"${total:,.2f}.")
    for i in items:
        qty = D(i.get("qty"))
        expect = cents(qty * D(i.get("unit_price")))
        # A unit price printed to more decimals (9.026 shown as $9.03) can be
        # off by up to half a cent per unit — that's the document's rounding.
        if qty != 1 and abs(expect - cents(i.get("line_total"))) > abs(qty) * Decimal("0.005"):
            warn.append(f"“{i.get('description', '')}”: {i.get('qty')} × "
                        f"${cents(i.get('unit_price')):,.2f} ≠ ${cents(i.get('line_total')):,.2f}.")
    return warn


# ── vendor parsers ───────────────────────────────────────────────────────────

# (name, detect(text) -> bool, parse(text, method) -> result). The first whose
# detect() matches handles the text; the heuristic parser is the fallback.
VENDOR_PARSERS: list = []


def register_vendor_parser(name: str, detect, parse, first: bool = False):
    entry = (name, detect, parse)
    VENDOR_PARSERS.insert(0, entry) if first else VENDOR_PARSERS.append(entry)


def parse_receipt_text(text: str, method: str = "text") -> dict:
    """Vendor parser for this layout if one matches, else the heuristic parser."""
    for name, detect, parse in VENDOR_PARSERS:
        try:
            if not detect(text):
                continue
            res = parse(text, method)
        except Exception:
            continue
        if res and (res["total"] or res["line_items"]):
            return res
    return parse_text(text, method)


# ── AI reader (Anthropic Messages API, opt-in) ───────────────────────────────

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2026-10-21"
DEFAULT_AI_MODEL = "claude-opus-5-5"

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "date": {"type": "string", "description": "YYYY-MM-DD, or empty if not shown"},
        "currency": {"type": "string", "description": "ISO 4217 code, e.g. USD"},
        "order_number": {"type": "string", "description": "Order / invoice number, or empty"},
        "subtotal": {"type": "number", "description": "Sum of the items only"},
        "shipping": {"type": "number", "description": "Shipping/handling, net of free shipping"},
        "discount": {"type": "number", "description": "Order-level discounts, as a positive number"},
        "duties": {"type": "number", "description": "Import duties, customs and other fees"},
        "tax": {"type": "number"},
        "tip": {"type": "number"},
        "total": {"type": "number"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "sku": {"type": "string", "description": "Part number / SKU, or empty"},
                    "qty": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "line_total": {"type": "number"},
                },
                "required": ["description", "sku", "qty", "unit_price", "line_total"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["vendor", "date", "currency", "order_number", "subtotal", "shipping",
                 "discount", "duties", "tax", "tip", "total", "line_items"],
    "additionalProperties": False,
}

_AI_PROMPT = (
    "Extract this receipt for bookkeeping. Return the merchant name as vendor, the "
    "purchase date as YYYY-MM-DD (empty string if absent; for a numeric date like 08/10/2026 "
    "use the vendor's country to decide day/month order), the currency code and the order "
    "or invoice number. subtotal is the sum of the purchased items only — never a subtotal "
    "that already includes shipping. shipping is shipping/handling net of any free-shipping "
    "credit; discount is order-level discounts, promotions and gift-card credit as a "
    "positive number; duties is import duties, customs and similar fees; then tax, tip and "
    "the grand total (0 for anything the receipt doesn't have). They must satisfy "
    "subtotal + shipping + duties + tax + tip - discount = total. List every purchased item "
    "as a line item with its part number/SKU if shown, quantity, unit price and line total "
    "(use the printed extended price as the line total). Do not include payment, change or "
    "card lines. Copy numbers exactly as printed — do not invent values.")


class AIReceiptError(Exception):
    """A user-presentable failure from the AI reader (never contains the key)."""


def ai_request_body(path: str, model: str = DEFAULT_AI_MODEL) -> dict:
    ext = _ext(path)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > 20 * 1024 * 1024:
        raise AIReceiptError("That file is larger than 20 MB — too big to send.")
    b64 = base64.standard_b64encode(data).decode("ascii")
    if ext == ".pdf":
        # Sent whole: the API reads image-only PDF pages visually too.
        block = {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    elif ext in _IMAGE_MEDIA:
        block = {"type": "image",
                 "source": {"type": "base64", "media_type": _IMAGE_MEDIA[ext], "data": b64}}
    else:
        raise AIReceiptError("AI reading works with JPG, PNG, GIF, WEBP and PDF files.")
    return {
        "model": model or DEFAULT_AI_MODEL,
        "max_tokens": 16000,
        "fallbacks": "default",
        "output_config": {"format": {"type": "json_schema", "schema": RECEIPT_SCHEMA}},
        "messages": [{"role": "user", "content": [block, {"type": "text", "text": _AI_PROMPT}]}],
    }


def parse_with_ai(path: str, api_key: str, model: str = DEFAULT_AI_MODEL,
                  timeout: float = 120.0, session=None) -> dict:
    """
    Read an image or scanned PDF with Claude. Raises AIReceiptError with a
    friendly message on any failure (offline, bad key, rate limit, bad JSON).
    """
    if not api_key:
        raise AIReceiptError("Add your Anthropic API key in Settings → AI Receipt Reading.")
    body = ai_request_body(path, model)
    try:
        import requests
    except Exception:
        raise AIReceiptError("The network library isn't available in this build.")
    http = session or requests
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION,
               "anthropic-beta": "server-side-fallback-2026-07-01",
               "content-type": "application/json"}
    try:
        resp = http.post(ANTHROPIC_URL, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise AIReceiptError("Couldn't reach Anthropic — check your internet connection.")
    except requests.exceptions.Timeout:
        raise AIReceiptError("Anthropic took too long to answer. Try again in a moment.")
    except requests.exceptions.RequestException as e:
        raise AIReceiptError(f"Network error: {type(e).__name__}")
    return parse_ai_response(resp.status_code, _safe_json(resp), resp.headers)


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None


def parse_ai_response(status: int, payload, headers=None) -> dict:
    """Turn a Messages API HTTP response into a receipt result (pure, testable)."""
    if status != 200:
        msg = ((payload or {}).get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
        if status == 401:
            raise AIReceiptError("Anthropic rejected the API key. Check it in Settings.")
        if status == 403:
            raise AIReceiptError("This API key isn't allowed to use that model.")
        if status == 404:
            raise AIReceiptError("Model not found — check the model name in Settings.")
        if status == 413:
            raise AIReceiptError("That file is too large for the API.")
        if status == 429:
            wait = (headers or {}).get("retry-after", "")
            raise AIReceiptError("Rate limited by Anthropic." + (f" Try again in {wait}s." if wait else ""))
        if status >= 500 or status == 529:
            raise AIReceiptError("Anthropic is having trouble right now. Try again shortly.")
        raise AIReceiptError(f"Anthropic returned an error ({status}). {msg[:200]}".strip())
    if not isinstance(payload, dict):
        raise AIReceiptError("Anthropic's reply wasn't valid JSON.")
    if payload.get("stop_reason") == "refusal":
        raise AIReceiptError("The model declined to read this file. Enter it manually.")
    if payload.get("stop_reason") == "max_tokens":
        raise AIReceiptError("The receipt was too long to read in one go. Enter it manually.")
    text = "".join(b.get("text", "") for b in payload.get("content", [])
                   if isinstance(b, dict) and b.get("type") == "text")
    try:
        data = json.loads(text)
    except ValueError:
        raise AIReceiptError("The AI reply couldn't be understood. Enter it manually.")
    return normalize_ai_result(data)


def normalize_ai_result(data: dict) -> dict:
    """Validate/convert the model's JSON into the standard result dict.
    Fields added in later versions (shipping, discount, duties, sku…) default
    to 0 / '' so replies in the older shape still work."""
    if not isinstance(data, dict):
        raise AIReceiptError("The AI reply had an unexpected shape.")
    res = empty_result("ai")
    res["vendor"] = str(data.get("vendor") or "")[:120]
    d = str(data.get("date") or "")
    try:
        res["date"] = date.fromisoformat(d).isoformat() if d else ""
    except ValueError:
        res["date"], raw = parse_date_info(d)
        if raw:
            res["date_note"] = ambiguous_date_note(raw)
    cur = str(data.get("currency") or "USD").upper()
    res["currency"] = cur if re.fullmatch(r"[A-Z]{3}", cur) else "USD"
    res["order_number"] = str(data.get("order_number") or "")[:60]
    for k in MONEY_FIELDS:
        try:
            res[k] = cents(data.get(k) or 0)
        except Exception:
            res[k] = ZERO
    res["discount"] = abs(res["discount"])
    items = []
    for it in data.get("line_items") or []:
        if not isinstance(it, dict):
            continue
        qty = D(it.get("qty") or 1) or Decimal(1)
        items.append({"description": str(it.get("description") or "").strip()[:250] or "Item",
                      "qty": qty, "unit_price": cents(it.get("unit_price") or 0),
                      "line_total": cents(it.get("line_total") or 0),
                      "sku": str(it.get("sku") or "").strip()[:60]})
    res["line_items"] = annotate_packs(items)
    res["warnings"] = check_totals(res)
    return res


# ── registry ─────────────────────────────────────────────────────────────────

def _text_parser(path: str, **_):
    text = extract_text(path)
    if not text.strip():
        return None
    res = parse_receipt_text(text, "text")
    return res if (res["total"] or res["line_items"]) else dict(res, method="text-unreadable")


def ocr_text(path: str) -> str:
    """OCR an image file, or the embedded page images of an image-only PDF."""
    from parsers import ocr
    import os
    import tempfile
    if _ext(path) != ".pdf":
        return ocr.ocr_image(path)
    with open(path, "rb") as f:
        doc = read_pdf(f.read())
    if doc is None or not doc.image_only:
        return ""
    texts = []
    for jpeg, _w, _h in doc.images[:5]:
        fd, tmp = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(jpeg)
            texts.append(ocr.ocr_image(tmp))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return "\n\n".join(t for t in texts if t.strip())


def _ocr_parser(path: str, use_ocr: bool = True, **_):
    from parsers import ocr
    if not use_ocr or not ocr.available():
        return None
    try:
        text = ocr_text(path)
    except ocr.OCRError:
        return None
    if not text.strip():
        return None
    res = parse_receipt_text(text, "ocr")
    return res if (res["total"] or res["line_items"]) else dict(res, method="text-unreadable")


def _ai_parser(path: str, api_key: str = "", model: str = DEFAULT_AI_MODEL, **_):
    if not api_key:
        return None
    return parse_with_ai(path, api_key, model)


# (name, can_handle(ext), fn(path, **opts) -> result | None). First non-None wins.
PARSERS = [
    ("text", lambda ext: ext in TEXT_EXTS, _text_parser),
    ("ocr", lambda ext: ext in OCR_EXTS or ext == ".pdf", _ocr_parser),
    ("ai", lambda ext: ext in IMAGE_EXTS or ext == ".pdf", _ai_parser),
]


def register_parser(name: str, can_handle, fn, first: bool = False):
    entry = (name, can_handle, fn)
    PARSERS.insert(0, entry) if first else PARSERS.append(entry)


def _is_image_pdf(path: str) -> bool | None:
    """True image-only, False has text/other, None unreadable (encrypted/corrupt)."""
    try:
        with open(path, "rb") as f:
            doc = read_pdf(f.read())
    except OSError:
        return None
    return None if doc is None else doc.image_only


def parse_file(path: str, use_ai: bool = False, api_key: str = "",
               model: str = DEFAULT_AI_MODEL, use_ocr: bool = True) -> dict:
    """
    Run the parsers in order. AI is used only when use_ai and api_key are set.
    AI errors propagate (AIReceiptError) so the UI can explain them; every other
    failure falls back to an empty result for manual entry. Can take a few
    seconds (OCR) — call it off the UI thread.
    """
    ext = _ext(path)
    unreadable = None
    for name, can, fn in PARSERS:
        if not can(ext):
            continue
        if name == "ai" and not use_ai:
            continue
        try:
            res = fn(path, api_key=api_key, model=model, use_ocr=use_ocr)
        except AIReceiptError:
            raise
        except Exception:
            continue
        if res is None:
            continue
        if res.get("method") != "text-unreadable":
            return res
        unreadable = unreadable or res
    res = empty_result("manual")
    if unreadable:
        for k in ("raw_text", "vendor", "date", "date_note", "currency"):
            res[k] = unreadable.get(k, res[k])
        res["warnings"] = [MSG_NO_TOTALS]
    elif ext in IMAGE_EXTS:
        res["warnings"] = [MSG_IMAGE]
    elif ext == ".pdf":
        kind = _is_image_pdf(path)
        res["warnings"] = [MSG_BAD_PDF if kind is None else MSG_IMAGE if kind else MSG_NO_TEXT_PDF]
    return res


from parsers import receipt_vendors  # noqa: E402,F401  (registers the vendor parsers)
