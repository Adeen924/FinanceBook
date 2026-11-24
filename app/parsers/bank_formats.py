"""
Known bank CSV export formats (v1.16).

Each BankFormat says where a bank's export keeps the date, payee, memo and
amount, and — the part that goes wrong most often — how the file says whether
money went out or came in:

  SIGNED              one Amount column, negative = money out
  DEBIT_CREDIT        separate Debit and Credit columns (magnitudes)
  TYPE_DECIDES        one unsigned Amount column + a Type column whose words
                      ("Debit"/"Credit", …) say which way the money went
  PURCHASES_POSITIVE  card files that list a purchase as a positive number and
                      a payment / refund as negative — the opposite of FinanceBook

parsers.spreadsheet.parse_spreadsheet detects the format from the file's header
row (or, for Wells Fargo's header-less export, from the shape of its rows) and
falls back to its generic column guessing for anything not listed here.

Adding a bank: put an anonymized export in tests/fixtures/bank_csv/, add an
entry below, add its expected rows to tests/test_bank_formats.py, then run
    python app/parsers/bank_formats.py --write
to regenerate docs/BANK_FORMATS.md (a test fails while it is out of date).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

SIGNED = "signed"
DEBIT_CREDIT = "debit_credit"
TYPE_DECIDES = "type_decides"
PURCHASES_POSITIVE = "purchases_positive"

SIGN_STYLE_TEXT = {
    SIGNED: "Signed amount (negative = money out)",
    DEBIT_CREDIT: "Separate Debit / Credit columns",
    TYPE_DECIDES: "Unsigned amount + type column (Debit / Credit)",
    PURCHASES_POSITIVE: "Purchases positive, payments negative (reversed)",
}

KIND_TEXT = {"checking": "checking", "credit": "credit card"}

# Words a bank's type / details column uses. "in" = money into the account
# (a deposit, or a payment / refund on a card); "out" = money out.
TYPE_WORDS_IN = frozenset({
    "credit", "cr", "deposit", "dep", "dslip", "payment", "return", "refund",
    "credit adjustment", "interest earned"})
TYPE_WORDS_OUT = frozenset({
    "debit", "dr", "withdrawal", "sale", "purchase", "check", "fee", "charge",
    "installment", "interest", "debit adjustment"})
# Present in type columns but can go either way — never counted as a mismatch.
TYPE_WORDS_EITHER = frozenset({"adjustment", "transfer", "other"})

# Payee words that mark a payment *to* a credit card.
CARD_PAYMENT_RE = re.compile(r"\b(PAYMENT|THANK\s*YOU|AUTOPAY|AUTO\s*PAY|AUTOMATIC PAY)",
                             re.IGNORECASE)


def normalize_header(cell) -> str:
    return re.sub(r"\s+", " ", str(cell if cell is not None else "")).strip().lower()


def header_signature(cells) -> tuple:
    """Normalized, non-empty header names in order (trailing empty columns —
    Chase's trailing comma — and NaN cells from Excel are ignored)."""
    out = []
    for c in cells:
        n = normalize_header(c)
        if n and n != "nan":
            out.append(n)
    return tuple(out)


@dataclass(frozen=True)
class BankFormat:
    id: str
    bank: str
    kind: str                       # "checking" | "credit"
    headers: tuple                  # header_signature(); () for header-less shapes
    date: str                       # column names are the *normalized* headers
    payee: str
    amount: str = ""
    debit: str = ""
    credit: str = ""
    type: str = ""                  # Debit/Credit, Sale/Payment, … column
    memo: str = ""
    category: str = ""
    check: str = ""                 # check number column → memo "Check #123"
    date_formats: tuple = ("%m/%d/%Y",)
    sign: str = SIGNED
    memo_junk: tuple = ()           # regexes: memo text matching one fully is dropped
    blank_payee: str = ""           # template when the description is blank
    skip_payee: str = ""            # regex: rows whose payee matches are not transactions
    headerless: str = ""            # name of a header-less shape matcher (see _SHAPES)
    columns: tuple = ()             # column names to use for a header-less file
    quirks: tuple = ()
    verified: bool = False
    fixture: str = ""
    added: str = "1.16.0"

    @property
    def label(self) -> str:
        return f"{self.bank} {KIND_TEXT.get(self.kind, self.kind)}"

    @property
    def display_bank(self) -> str:
        return self.bank + (" (no header row)" if self.headerless else "")

    def parse_date(self, text: str) -> str | None:
        text = (text or "").strip()
        for fmt in self.date_formats:
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None


_USB_HEADERS = ("date", "transaction", "name", "memo", "amount")
_WF_HEADERS = ("date", "description", "amount", "check #", "status")
_WF_NOHEADER_COLS = ("date", "amount", "*", "check #", "description")
_USB_JUNK = (r"download from usbank\.com\.?",)

REGISTRY: tuple[BankFormat, ...] = (
    BankFormat(
        id="capitalone_checking", bank="Capital One", kind="checking",
        headers=("account number", "transaction description", "transaction date",
                 "transaction type", "transaction amount", "balance"),
        date="transaction date", payee="transaction description",
        amount="transaction amount", type="transaction type",
        date_formats=("%m/%d/%y", "%m/%d/%Y"), sign=TYPE_DECIDES,
        quirks=("Amount is always positive; the Transaction Type column (Debit / Credit) "
                "decides the sign — before 1.16 every row imported as money in",
                "Two-digit years", "No newline at end of file"),
        verified=True, fixture="capitalone_checking.csv"),
    BankFormat(
        id="capitalone_credit", bank="Capital One", kind="credit",
        headers=("transaction date", "posted date", "card no.", "description",
                 "category", "debit", "credit"),
        date="transaction date", payee="description", debit="debit", credit="credit",
        category="category", date_formats=("%m/%d/%y", "%m/%d/%Y"), sign=DEBIT_CREDIT,
        blank_payee="Capital One {what} ({category})",
        quirks=("Some rows have a blank Description; the payee becomes "
                "\"Capital One purchase (<Category>)\"",
                "Two-digit years", "Windows (CRLF) line endings"),
        verified=True, fixture="capitalone_credit.csv"),
    BankFormat(
        id="chase_checking", bank="Chase", kind="checking",
        headers=("details", "posting date", "description", "amount", "type",
                 "balance", "check or slip #"),
        date="posting date", payee="description", amount="amount", type="details",
        check="check or slip #", sign=SIGNED,
        quirks=("Every row ends with an extra comma (one more field than the header)",
                "Details (DEBIT / CREDIT / CHECK / DSLIP) is checked against the sign"),
        verified=True, fixture="chase_checking.csv"),
    BankFormat(
        id="chase_credit", bank="Chase", kind="credit",
        headers=("transaction date", "post date", "description", "category", "type",
                 "amount", "memo"),
        date="transaction date", payee="description", amount="amount", type="type",
        memo="memo", category="category", sign=SIGNED,
        quirks=("HTML entities in names (\"BEST PIZZA &amp; BREW\") are unescaped",
                "Type (Sale / Payment / Return / Adjustment) is checked against the sign"),
        verified=True, fixture="chase_credit.csv"),
    BankFormat(
        id="usbank_checking", bank="U.S. Bank", kind="checking", headers=_USB_HEADERS,
        date="date", payee="name", amount="amount", type="transaction", memo="memo",
        date_formats=("%Y-%m-%d",), sign=SIGNED, memo_junk=_USB_JUNK,
        quirks=("Memo \"Download from usbank.com.\" is dropped",
                "Repeated spaces in names are collapsed",
                "$0.00 rows are skipped"),
        verified=True, fixture="usbank_checking.csv"),
    BankFormat(
        id="usbank_credit", bank="U.S. Bank", kind="credit", headers=_USB_HEADERS,
        date="date", payee="name", amount="amount", type="transaction", memo="memo",
        date_formats=("%Y-%m-%d",), sign=SIGNED, memo_junk=_USB_JUNK,
        quirks=("Memo reference strings (\"24692…; 05942; ; ; ;\") are dropped; "
                "words such as \"WEB AUTOMTC\" are kept",
                "$0.00 rows (e.g. a waived annual fee) are skipped",
                "Same layout as U.S. Bank checking — the account you import into picks"),
        verified=True, fixture="usbank_credit.csv"),
    BankFormat(
        id="wellsfargo_checking", bank="Wells Fargo", kind="checking", headers=_WF_HEADERS,
        date="date", payee="description", amount="amount", check="check #", sign=SIGNED,
        quirks=("Check numbers go to the memo",),
        verified=True, fixture="wellsfargo_checking.csv"),
    BankFormat(
        id="wellsfargo_credit", bank="Wells Fargo", kind="credit", headers=_WF_HEADERS,
        date="date", payee="description", amount="amount", check="check #", sign=SIGNED,
        quirks=("Same layout as Wells Fargo checking — the account you import into picks",),
        verified=True, fixture="wellsfargo_credit.csv"),
    BankFormat(
        id="wellsfargo_checking_noheader", bank="Wells Fargo", kind="checking",
        headers=(), headerless="wellsfargo", columns=_WF_NOHEADER_COLS,
        date="date", payee="description", amount="amount", check="check #", sign=SIGNED,
        quirks=("No header row: date, amount, \"*\", check #, description",),
        verified=False, fixture="wellsfargo_checking_noheader.csv"),
    BankFormat(
        id="wellsfargo_credit_noheader", bank="Wells Fargo", kind="credit",
        headers=(), headerless="wellsfargo", columns=_WF_NOHEADER_COLS,
        date="date", payee="description", amount="amount", check="check #", sign=SIGNED,
        quirks=("No header row: date, amount, \"*\", check #, description",),
        verified=False, fixture="wellsfargo_credit_noheader.csv"),
    BankFormat(
        id="bankofamerica_checking", bank="Bank of America", kind="checking",
        headers=("date", "description", "amount", "running bal."),
        date="date", payee="description", amount="amount", sign=SIGNED,
        skip_payee=r"^(beginning|ending) balance as of\b",
        quirks=("Summary block (beginning / ending balance, totals) above the header is skipped",
                "\"Beginning balance as of …\" row with no amount is skipped"),
        verified=False, fixture="bankofamerica_checking.csv"),
    BankFormat(
        id="bankofamerica_credit", bank="Bank of America", kind="credit",
        headers=("posted date", "reference number", "payee", "address", "amount"),
        date="posted date", payee="payee", amount="amount", sign=SIGNED,
        quirks=("Reference Number and Address are not imported",),
        verified=False, fixture="bankofamerica_credit.csv"),
    BankFormat(
        id="applecard_credit", bank="Apple Card", kind="credit",
        headers=("transaction date", "clearing date", "description", "merchant",
                 "category", "type", "amount (usd)", "purchased by"),
        date="transaction date", payee="merchant", memo="description", amount="amount (usd)",
        type="type", category="category", sign=PURCHASES_POSITIVE,
        quirks=("Purchases are positive and payments / credits negative — signs are reversed",
                "Payee is the clean Merchant name; the bank's raw Description goes to the memo",
                "Type (Purchase / Payment / Credit / Installment) is checked against the sign"),
        verified=False, fixture="applecard_credit.csv"),
    BankFormat(
        id="bmo_checking", bank="BMO (U.S.)", kind="checking",
        headers=("posted date", "description", "amount", "currency",
                 "transaction reference number", "fi transaction reference", "type",
                 "credit/debit", "original amount"),
        date="posted date", payee="description", amount="amount", type="credit/debit",
        date_formats=("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"), sign=TYPE_DECIDES,
        quirks=("BMO U.S. (formerly BMO Harris / Bank of the West) checking export: "
                "Type is \"DDA Debit\" / \"DDA Deposit\" / \"DDA Credit\"",
                "Amount is signed, and the CREDIT/DEBIT column decides the sign anyway, "
                "so an export with unsigned amounts reads right too",
                "Runs of spaces in descriptions (\"PPD  ACME PAYROLL\") are collapsed",
                "Reference numbers, Currency and Original Amount are not imported",
                "Lines of text above the header row are skipped; YYYYMMDD dates are read",
                "Windows (CRLF) line endings"),
        verified=True, fixture="bmo_checking.csv", added="1.19.0"),
)

BY_ID = {f.id: f for f in REGISTRY}


# ── header-less shapes ────────────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
_AMOUNT_RE = re.compile(r"^-?\$?[\d,]*\.?\d+$")


def _wellsfargo_shape(rows) -> bool:
    """Wells Fargo's classic export: no header, five columns —
    "date","amount","*","check #","description"."""
    data = [r for r in rows if any(str(c).strip() for c in r)]
    if not data:
        return False
    for r in data[:20]:
        cells = [str(c).strip() for c in r]
        while cells and not cells[-1]:
            cells.pop()
        if len(cells) != 5:
            return False
        if not (_DATE_RE.match(cells[0]) and _AMOUNT_RE.match(cells[1].replace(" ", ""))
                and cells[2] == "*" and cells[4]):
            return False
    return True


_SHAPES = {"wellsfargo": _wellsfargo_shape}


# ── detection ─────────────────────────────────────────────────────────────────

def _pick_kind(candidates, account_kind):
    for f in candidates:
        if f.kind == account_kind:
            return f
    return candidates[0]


def detect(rows, account_kind: str | None = None):
    """(BankFormat, header_row_index) for a raw grid of cells, or (None, None).
    header_row_index is -1 for header-less formats (every row is data).
    account_kind ("checking" / "credit") picks between formats that share one
    layout (U.S. Bank, Wells Fargo)."""
    by_sig: dict[tuple, list] = {}
    for f in REGISTRY:
        if f.headers:
            by_sig.setdefault(f.headers, []).append(f)
    for i, r in enumerate(rows[:25]):
        cands = by_sig.get(header_signature(r))
        if cands:
            return _pick_kind(cands, account_kind), i
    for name, matcher in _SHAPES.items():
        if matcher(rows):
            cands = [f for f in REGISTRY if f.headerless == name]
            return _pick_kind(cands, account_kind), -1
    return None, None


def type_direction(word: str, kind: str | None = None) -> str:
    """'in', 'out' or '' for a value of a bank's type / details column.
    "Payment" is money in on a card (paying it off) but could be a bill paid
    from a checking account, so it says nothing there."""
    w = normalize_header(word)
    if w == "payment" and kind == "checking":
        return ""
    if w in TYPE_WORDS_IN:
        return "in"
    if w in TYPE_WORDS_OUT:
        return "out"
    return ""


def is_type_word(word: str) -> bool:
    w = normalize_header(word)
    return w in TYPE_WORDS_IN or w in TYPE_WORDS_OUT or w in TYPE_WORDS_EITHER


def card_file_looks_reversed(rows) -> bool:
    """Card-account sanity check: most purchases came in as money IN, and the
    card payments (PAYMENT / THANK YOU / AUTOPAY) — if any — as money OUT."""
    purchases = [float(r["amount"]) for r in rows
                 if not CARD_PAYMENT_RE.search(r.get("payee") or "")]
    payments = [float(r["amount"]) for r in rows
                if CARD_PAYMENT_RE.search(r.get("payee") or "")]
    if len(purchases) < 2:
        return False
    purchases_in = sum(1 for a in purchases if a > 0) > len(purchases) / 2
    if not purchases_in:
        return False
    if payments:
        return sum(1 for a in payments if a < 0) > len(payments) / 2
    return True


# ── docs ──────────────────────────────────────────────────────────────────────

def render_markdown() -> str:
    lines = [
        "# Supported bank CSV formats",
        "",
        "<!-- Generated from app/parsers/bank_formats.py — do not edit by hand.",
        "     Regenerate with:  python app/parsers/bank_formats.py --write -->",
        "",
        "FinanceBook recognises these exports from the file's header row and reads "
        "the sign the way each bank writes it. Any other CSV / Excel file still "
        "imports with the generic column detection. **Verified** formats were checked "
        "against a real export; unverified ones are built from the bank's documented "
        "layout — please report a problem if one imports wrong.",
        "",
        "| Bank | Account kind | Sign style | Quirks handled | Verified | Fixture | Added |",
        "|---|---|---|---|---|---|---|",
    ]
    def md(text):           # literal text inside a Markdown table cell
        return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("|", "\\|"))

    for f in REGISTRY:
        quirks = "<br>".join(md(q) for q in f.quirks) or "—"
        lines.append(
            f"| {md(f.display_bank)} | {KIND_TEXT[f.kind]} | {SIGN_STYLE_TEXT[f.sign]} | {quirks} | "
            f"{'Y' if f.verified else 'N'} | `tests/fixtures/bank_csv/{f.fixture}` | "
            f"{f.added} |")
    lines += [
        "",
        "## Adding a bank",
        "",
        "1. Export a CSV from the bank and anonymize it: replace people's names, "
        "account / card / reference numbers and balances with fake ones, but keep "
        "the header row, column order, quoting, date format, sign style, merchant "
        "names and every kind of row (purchase, payment, refund, fee, $0.00, blank "
        "description).",
        "2. Save it as `tests/fixtures/bank_csv/<bank>_<checking|credit>.csv`.",
        "3. Add a `BankFormat` entry to `REGISTRY` in `app/parsers/bank_formats.py`.",
        "4. Add the expected rows to `FIXTURE_EXPECTATIONS` in `tests/test_bank_formats.py`.",
        "5. Run `python app/parsers/bank_formats.py --write` and the test suite.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":      # pragma: no cover
    import sys
    from pathlib import Path
    doc = Path(__file__).resolve().parents[2] / "docs" / "BANK_FORMATS.md"
    if "--write" in sys.argv:
        doc.write_text(render_markdown(), encoding="utf-8", newline="\n")
        print(f"wrote {doc}")
    else:
        print(render_markdown())
