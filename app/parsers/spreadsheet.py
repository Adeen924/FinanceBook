"""
Parse CSV and XLSX bank export files.
Known bank exports are recognised from their header row (parsers/bank_formats.py);
anything else auto-detects columns, with manual mapping as a fallback.
"""
import csv
import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO, StringIO

import pandas as pd

from parsers import bank_formats


COMMON_DATE_COLS = ["date", "transaction date", "posted date", "trans date", "trans. date", "posting date"]
COMMON_PAYEE_COLS = ["payee", "name", "description", "transaction description", "details", "merchant"]
COMMON_MEMO_COLS = ["memo", "notes", "note"]
COMMON_SPLIT_COLS = ["split"]
COMMON_AMOUNT_COLS = ["amount", "transaction amount", "debit/credit"]
COMMON_DEBIT_COLS = ["debit", "withdrawal", "withdrawals", "charges"]
COMMON_CREDIT_COLS = ["credit", "deposit", "deposits", "payments"]


def _normalize(s: str) -> str:
    return str(s).lower().strip()


def _find_idx(headers: list[str], candidates) -> int | None:
    """Index of the header matching the first candidate found (candidates in
    priority order). Same choice v1.15's column detection made — the duplicate
    key relies on that."""
    norm = {_normalize(h): i for i, h in enumerate(headers)}
    for cand in candidates:
        if _normalize(cand) in norm:
            return norm[_normalize(cand)]
    return None


def _detect_header_row(rows) -> int | None:
    """
    Index of the row that looks like the column-header row — i.e. the first row
    (within the first 25) that contains a recognizable Date column. Lets us skip
    the title/company/report-name rows many bank & QuickBooks exports put on top.
    """
    for i in range(min(25, len(rows))):
        cells = [_normalize(c) for c in rows[i]]
        if any(c in COMMON_DATE_COLS for c in cells if c and c != "nan"):
            return i
    return None


def _parse_amount(val) -> float | None:
    if val is None or (not isinstance(val, str) and pd.isna(val)):
        return None
    if str(val).strip().lower() in ("", "-", "n/a", "nan", "none"):
        return None
    s = re.sub(r"[,$\s]", "", str(val))
    s = s.replace("(", "-").replace(")", "")
    try:
        return float(s)
    except ValueError:
        return None


def debit_credit_amount(debit: float | None, credit: float | None) -> float | None:
    """Signed amount from separate Debit / Credit columns: debits are money out
    (negative) and credits money in (positive), whatever sign the bank wrote
    them with — some banks write debits as "-50.00" or "(50.00)", which must not
    turn a withdrawal into a deposit. None when both are empty / zero."""
    debit, credit = abs(debit or 0.0), abs(credit or 0.0)
    if not (debit or credit):
        return None
    return credit - debit


# ── text clean-up ─────────────────────────────────────────────────────────────

def clean_text(s: str) -> str:
    """HTML entities unescaped ("&amp;" → "&") and runs of spaces collapsed."""
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", s).strip()


_LONG_NUMBER = re.compile(r"^[\d\s.\-#/]{12,}$")


def clean_memo(memo: str, junk_patterns=()) -> str:
    """Drop memo text that is only noise: a bank's boilerplate ("Download from
    usbank.com."), ";"-separated reference strings ("2469…; 05942; ; ; ;" keeps
    only the parts with letters) and long bare reference numbers."""
    memo = clean_text(memo)
    if not memo:
        return ""
    for pat in junk_patterns:
        if re.fullmatch(pat, memo, re.IGNORECASE):
            return ""
    if ";" in memo:
        parts = [p.strip() for p in memo.split(";")]
        memo = "; ".join(p for p in parts if re.search(r"[A-Za-z]", p))
    if _LONG_NUMBER.match(memo):
        return ""
    return memo


def _blankish(v) -> str:
    v = "" if v is None else str(v).strip()
    return "" if v.lower() in ("nan", "none") else v


# ── reading ───────────────────────────────────────────────────────────────────

def _read_grid(file_bytes: bytes, filename: str) -> list[list[str]]:
    """The file as a list of rows of strings (no header handling)."""
    if filename.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(BytesIO(file_bytes), dtype=str, header=None)
        return [["" if (not isinstance(v, str) and pd.isna(v)) else str(v) for v in r]
                for r in raw.itertuples(index=False)]
    # Use the csv module: pandas' header=None keys the column count off the
    # first line, so a short title row on top would make it drop the wider
    # header/data rows. Reading rows ourselves and padding avoids that.
    text = file_bytes.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(StringIO(text)))
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def account_kind_for(account_type: str) -> str:
    """'credit' for credit-card accounts, 'checking' for everything else."""
    return "credit" if (account_type or "").strip().lower() in ("credit", "credit card") \
        else "checking"


# ── result ────────────────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # problems worth a popup
    notes: list = field(default_factory=list)      # informational
    format: object = None                          # bank_formats.BankFormat or None
    type_mismatches: int = 0     # rows whose type column disagrees with the sign
    looks_reversed: bool = False  # card-account sanity check fired
    kind_mismatch: str = ""
    skipped_zero: int = 0

    @property
    def format_label(self) -> str:
        return self.format.label if self.format else "Unknown — generic"

    @property
    def format_id(self) -> str:
        return self.format.id if self.format else "generic"

    @property
    def money_in(self) -> tuple[int, float]:
        a = [float(r["amount"]) for r in self.rows if float(r["amount"]) > 0]
        return len(a), round(sum(a), 2)

    @property
    def money_out(self) -> tuple[int, float]:
        a = [float(r["amount"]) for r in self.rows if float(r["amount"]) < 0]
        return len(a), round(sum(a), 2)


@dataclass
class _Plan:
    """Column indexes and rules for one file."""
    date: int | None = None
    payee: int | None = None
    memo: int | None = None
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    type: int | None = None
    category: int | None = None
    check: int | None = None
    sign: str = bank_formats.SIGNED
    kind: str | None = None           # kind the type words are read for
    date_formats: tuple = ()
    memo_junk: tuple = ()
    blank_payee: str = ""
    skip_payee: str = ""
    # How v1.15's generic detection read the file — the duplicate key keeps
    # using it so files imported before 1.16 aren't imported a second time.
    legacy_payee: int | None = None
    legacy_memo: int | None = None
    legacy_amount: int | None = None
    legacy_debit: int | None = None
    legacy_credit: int | None = None


_TYPE_COL_NAMES = ["type", "transaction type", "details", "transaction", "credit/debit",
                   "debit/credit indicator", "cr/dr", "dr/cr", "transaction code"]


def _legacy_cols(plan: _Plan, headers: list[str]):
    plan.legacy_payee = _find_idx(headers, COMMON_PAYEE_COLS)
    plan.legacy_memo = _find_idx(headers, COMMON_MEMO_COLS)
    plan.legacy_amount = _find_idx(headers, COMMON_AMOUNT_COLS)
    plan.legacy_debit = _find_idx(headers, COMMON_DEBIT_COLS)
    plan.legacy_credit = _find_idx(headers, COMMON_CREDIT_COLS)


def _plan_for_format(fmt, headers: list[str]) -> _Plan:
    norm = [bank_formats.normalize_header(h) for h in headers]

    def idx(name):
        return norm.index(name) if name and name in norm else None

    plan = _Plan(date=idx(fmt.date), payee=idx(fmt.payee), memo=idx(fmt.memo),
                 amount=idx(fmt.amount), debit=idx(fmt.debit), credit=idx(fmt.credit),
                 type=idx(fmt.type), category=idx(fmt.category), check=idx(fmt.check),
                 sign=fmt.sign, kind=fmt.kind, date_formats=fmt.date_formats,
                 memo_junk=fmt.memo_junk, blank_payee=fmt.blank_payee,
                 skip_payee=fmt.skip_payee)
    if fmt.headers:
        _legacy_cols(plan, headers)
    else:                       # v1.15 couldn't read header-less files at all
        plan.legacy_payee, plan.legacy_amount = plan.payee, plan.amount
    return plan


def _plan_generic(headers: list[str], data_rows, col_mapping, account_kind) -> _Plan:
    plan = _Plan(kind=account_kind)
    if col_mapping:
        def m(key):
            name = col_mapping.get(key)
            return _find_idx(headers, [name]) if name else None
        plan.date, plan.payee, plan.amount = m("date"), m("payee"), m("amount")
        plan.debit, plan.credit, plan.memo = m("debit"), m("credit"), m("memo")
        split = m("split")
    else:
        plan.date = _find_idx(headers, COMMON_DATE_COLS)
        plan.payee = _find_idx(headers, COMMON_PAYEE_COLS)
        plan.amount = _find_idx(headers, COMMON_AMOUNT_COLS)
        plan.debit = _find_idx(headers, COMMON_DEBIT_COLS)
        plan.credit = _find_idx(headers, COMMON_CREDIT_COLS)
        plan.memo = _find_idx(headers, COMMON_MEMO_COLS)
        plan.category = _find_idx(headers, ["category"])
        split = _find_idx(headers, COMMON_SPLIT_COLS)
    plan.legacy_payee, plan.legacy_memo = plan.payee, plan.memo
    plan.legacy_amount, plan.legacy_debit, plan.legacy_credit = \
        plan.amount, plan.debit, plan.credit

    # A "Split" column marks a QuickBooks export. Those are handled by the
    # dedicated Import Wizard, which understands both shapes — grouped
    # "Transaction Detail by Account" reports (account = section header, Split =
    # offsetting category) and flat exports (account = the Split column). Send the
    # user there rather than importing everything into one account here.
    if split is not None:
        raise ValueError(
            "This looks like a QuickBooks export (it has a 'Split' column).\n\n"
            "Import it from Settings → Open Import Wizard → Step 2 "
            "(Transactions), which understands the Split column — including "
            "using it as the account when the file isn't grouped by account.")

    if plan.amount is not None:
        plan.sign = bank_formats.SIGNED
        # A Type / Details column holding Debit/Credit, Sale/Payment, … words:
        # it decides the sign when every amount is unsigned, otherwise it's
        # only used to check the signs.
        taken = {plan.date, plan.payee, plan.amount, plan.memo}
        for name in _TYPE_COL_NAMES:
            ti = _find_idx(headers, [name])
            if ti is None or ti in taken:
                continue
            vals = [_blankish(r[ti]) for r in data_rows if ti < len(r) and _blankish(r[ti])]
            if not vals:
                continue
            words = sum(1 for v in vals if bank_formats.is_type_word(v))
            directed = sum(1 for v in vals if bank_formats.type_direction(v, account_kind))
            if words >= 0.8 * len(vals) and directed:
                plan.type = ti
                amts = [_parse_amount(r[plan.amount]) for r in data_rows
                        if plan.amount < len(r)]
                if not any(a is not None and a < 0 for a in amts):
                    plan.sign = bank_formats.TYPE_DECIDES
                break
    elif plan.debit is not None or plan.credit is not None:
        plan.sign = bank_formats.DEBIT_CREDIT
    return plan


def _cell(row, i):
    return _blankish(row[i]) if i is not None and i < len(row) else ""


def _parse_date(text: str, formats) -> str | None:
    for f in formats:
        try:
            return datetime.strptime(text, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return pd.to_datetime(text, dayfirst=False).strftime("%Y-%m-%d")
    except Exception:
        return None


def _hash(account_id, date_str, amount, payee) -> str:
    return hashlib.md5(f"{account_id}|{date_str}|{amount}|{payee}".encode()).hexdigest()[:16]


def analyze_spreadsheet(file_bytes: bytes, filename: str, account_id: str,
                        col_mapping: dict = None, reverse_signs: bool = False,
                        account_kind: str | None = None) -> ParseResult:
    """
    Parse a CSV / XLSX bank export into transaction dicts (money out negative,
    money in positive) plus what the import preview shows about it.

    account_kind ("checking" / "credit") picks between banks whose checking and
    card exports look the same, and turns on the card-account sanity check.
    col_mapping overrides detection: {"date": "Date", "payee": "Payee", ...}.
    reverse_signs ("Flip all signs") negates every amount.
    """
    res = ParseResult()
    rows = _read_grid(file_bytes, filename)
    if not rows or not any(any(str(c).strip() for c in r) for r in rows):
        res.warnings.append("The file appears to be empty.")
        return res

    fmt, header_idx = (None, None) if col_mapping else bank_formats.detect(rows, account_kind)
    if fmt is not None:
        res.format = fmt
        if header_idx == -1:
            headers, data = list(fmt.columns), rows
        else:
            headers, data = rows[header_idx], rows[header_idx + 1:]
        plan = _plan_for_format(fmt, headers)
        if account_kind and fmt.kind != account_kind:
            what = "a credit-card" if fmt.kind == "credit" else "a checking"
            acct = "credit-card" if account_kind == "credit" else "checking / savings"
            res.kind_mismatch = (f"This looks like {what} export ({fmt.label}), but you're "
                                 f"importing it into a {acct} account.")
    else:
        header_idx = _detect_header_row(rows)
        if header_idx is None:
            header_idx = 0  # fall back to treating the first row as the header
        headers = [str(c).strip() for c in rows[header_idx]]
        data = rows[header_idx + 1:]
        plan = _plan_generic(headers, data, col_mapping, account_kind)
        if plan.date is None:
            res.warnings.append("Could not detect a Date column.")
        if plan.payee is None and plan.memo is None:
            res.warnings.append("Could not detect a Description/Payee column.")
        if plan.amount is None and plan.debit is None and plan.credit is None:
            res.warnings.append("Could not detect an Amount column.")

    first_line = header_idx + 2 if header_idx >= 0 else 1
    for n, row in enumerate(data):
        line = first_line + n
        date_val = _cell(row, plan.date)
        if not date_val:
            continue
        date_str = _parse_date(date_val, plan.date_formats)
        if not date_str:
            res.warnings.append(f"Row {line}: unrecognized date '{date_val}', skipped.")
            continue

        raw_payee = _cell(row, plan.payee)
        if plan.skip_payee and re.search(plan.skip_payee, raw_payee, re.IGNORECASE):
            continue
        tword = _cell(row, plan.type)
        direction = bank_formats.type_direction(tword, plan.kind) if tword else ""

        # ── amount ──
        hash_amount = None
        if plan.sign == bank_formats.DEBIT_CREDIT:
            debit = _parse_amount(_cell(row, plan.debit))
            credit = _parse_amount(_cell(row, plan.credit))
            if debit is None and credit is None:
                amount = None
            else:
                amount = debit_credit_amount(debit, credit) or 0.0
                # The duplicate check hashes the amount as versions before 1.15
                # computed it (credit - debit as written).
                hash_amount = (credit or 0.0) - (debit or 0.0)
        else:
            amount = _parse_amount(_cell(row, plan.amount))
            if amount is not None:
                if plan.sign == bank_formats.TYPE_DECIDES:
                    if direction == "out":
                        amount = -abs(amount)
                    elif direction == "in":
                        amount = abs(amount)
                    else:
                        res.warnings.append(
                            f"Row {line}: unknown transaction type '{tword}' — "
                            f"kept the amount as written.")
                elif plan.sign == bank_formats.PURCHASES_POSITIVE:
                    amount = -amount

        if amount is None:
            res.warnings.append(f"Row {line}: no amount found, skipped.")
            continue
        if amount == 0:
            # A $0.00 line (a waived fee, a $0 authorization) moves no money;
            # importing it would only clutter the register.
            res.skipped_zero += 1
            continue
        if reverse_signs:
            amount = -amount
            hash_amount = None
        if hash_amount is None:
            hash_amount = amount
        if direction and (direction == "out") != (amount < 0):
            res.type_mismatches += 1

        # ── payee / memo ──
        payee = clean_text(raw_payee)
        memo = clean_memo(_cell(row, plan.memo), plan.memo_junk)
        check = _cell(row, plan.check)
        if check:
            memo = f"Check #{check}" + (f"; {memo}" if memo else "")
        if not payee:
            category = clean_text(_cell(row, plan.category))
            if plan.blank_payee:
                payee = plan.blank_payee.format(
                    what="purchase" if amount < 0 else "credit",
                    category=category or "Uncategorized")
            elif memo:
                payee, memo = memo, ""
            elif category:
                payee = category
            else:
                payee = "(no description)"

        # ── duplicate key ──
        # Built from the payee and amount exactly as v1.15 read them, so a file
        # imported before 1.16 is recognised. Where v1.15 got the sign wrong
        # (Capital One checking, Apple Card) the key uses the corrected amount
        # and _legacy_hash carries v1.15's, so the preview can fix that row.
        legacy_payee = _cell(row, plan.legacy_payee) or _cell(row, plan.legacy_memo)
        legacy_amount = legacy_hash_amount = None
        if plan.legacy_amount is not None:
            legacy_amount = _parse_amount(_cell(row, plan.legacy_amount))
            if legacy_amount is not None and reverse_signs:
                legacy_amount = -legacy_amount
            legacy_hash_amount = legacy_amount
        elif plan.legacy_debit is not None or plan.legacy_credit is not None:
            d = _parse_amount(_cell(row, plan.legacy_debit)) or 0.0
            c = _parse_amount(_cell(row, plan.legacy_credit)) or 0.0
            legacy_amount = debit_credit_amount(d, c)
            legacy_hash_amount = c - d
        legacy_hash = ""
        if legacy_amount is not None and round(legacy_amount, 2) == round(amount, 2):
            hash_amount = legacy_hash_amount
        elif legacy_amount is not None:
            legacy_hash = _hash(account_id, date_str, legacy_hash_amount, legacy_payee)
        import_hash = _hash(account_id, date_str, hash_amount, legacy_payee)

        txn = {
            "date": date_str,
            "account_id": account_id,
            "payee": payee,
            "memo": memo,
            "amount": str(round(amount, 2)),
            "category_id": "",
            "class_id": "",
            "is_transfer": "0",
            "transfer_pair_id": "",
            "reconciled": "0",
            "reconcile_id": "",
            "notes": "",
            "import_hash": import_hash,
        }
        if legacy_hash:
            txn["_legacy_hash"] = legacy_hash
        res.rows.append(txn)

    if res.skipped_zero:
        res.notes.append(f"Skipped {res.skipped_zero} $0.00 row(s) — they don't move any money.")
    if account_kind == "credit":
        res.looks_reversed = bank_formats.card_file_looks_reversed(res.rows)
    return res


def parse_spreadsheet(file_bytes: bytes, filename: str, account_id: str,
                      col_mapping: dict = None,
                      reverse_signs: bool = False,
                      account_kind: str | None = None) -> tuple[list[dict], list[str]]:
    """
    Parse CSV or XLSX. Returns (transactions, warnings) — see analyze_spreadsheet
    for the import preview's fuller result.
    Known bank exports (parsers/bank_formats.py) are read the way that bank
    writes them; anything else goes through generic column detection.
    reverse_signs: negate every amount ("Flip all signs").
    """
    res = analyze_spreadsheet(file_bytes, filename, account_id, col_mapping,
                              reverse_signs, account_kind)
    return res.rows, res.warnings


def parse_qb_multi_account_excel(file_bytes: bytes) -> tuple[list[dict], list[str]]:
    """
    Parse a QuickBooks 'Transaction Detail by Account' Excel export.

    QB formats this report with account names as section header rows between
    groups of transactions — there is no 'Account' column on each row.
    The file may contain multiple sheets; this function auto-detects the one
    with transaction data by looking for a row that contains 'Date'.
    Returns flat transaction dicts each with an '_account_name' field.
    """
    warnings: list[str] = []
    transactions: list[dict] = []

    # ── Auto-detect which sheet has the data ──────────────────────────────────
    import openpyxl
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    target_sheet = 0
    for idx, sname in enumerate(wb.sheetnames):
        ws = wb[sname]
        for row in ws.iter_rows(max_row=10, values_only=True):
            if any(str(c or "").strip().lower() == "date" for c in row):
                target_sheet = idx
                break
        else:
            continue
        break
    wb.close()

    df_raw = pd.read_excel(BytesIO(file_bytes), dtype=str, header=None,
                           sheet_name=target_sheet)

    # ── Find the column-header row ────────────────────────────────────────────
    header_idx = None
    date_col = name_col = memo_col = split_col = None
    debit_col = credit_col = amount_col = None

    for i, row in df_raw.iterrows():
        cells_lower = [str(c).strip().lower() for c in row]
        if "date" in cells_lower:
            header_idx = i
            for j, cell in enumerate(cells_lower):
                if cell == "date"   and date_col   is None: date_col   = j
                if cell in ("name", "payee", "description") and name_col   is None: name_col   = j
                if cell == "memo"                           and memo_col   is None: memo_col   = j
                if cell == "split"                         and split_col  is None: split_col  = j
                if cell in ("debit", "withdrawal")         and debit_col  is None: debit_col  = j
                if cell in ("credit", "deposit")           and credit_col is None: credit_col = j
                if cell == "amount"                        and amount_col is None: amount_col = j
            break

    if header_idx is None or date_col is None:
        warnings.append(
            "Could not find a header row with a 'Date' column. "
            "Make sure you exported 'Transaction Detail by Account' to Excel.")
        return [], warnings

    # ── Walk data rows ────────────────────────────────────────────────────────
    # Two export shapes are supported:
    #   • Grouped "Transaction Detail by Account": account names sit on their own
    #     section-header rows above each block, and the "Split" column holds the
    #     *offsetting* account/category. current_account comes from the headers.
    #   • Flat (e.g. a single-account or ungrouped export): there are no section
    #     headers, so the account each line belongs to is named in the "Split"
    #     column — QuickBooks labels the offsetting account "Split". We detect the
    #     absence of headers below and use Split as the account instead.
    # Account assignment is deferred to a second pass so we can tell which shape
    # this is (whether a section header was ever seen) before committing.
    current_account = ""
    section_header_seen = False
    records: list[dict] = []

    for i, row in df_raw.iterrows():
        if i <= header_idx:
            continue

        cells = [str(c).strip() for c in row]

        # Skip fully empty rows
        if all(c.lower() in ("", "nan", "none") for c in cells):
            continue

        # First non-empty cell — account headers can be in any column
        first_val = ""
        for c in cells:
            if c.lower() not in ("", "nan", "none"):
                first_val = c
                break

        date_val = cells[date_col] if date_col < len(cells) else ""

        # Skip total / subtotal / balance summary rows
        if first_val.lower().startswith("total") or first_val.lower() == "balance":
            continue

        # Try parsing the date column
        date_str = None
        if date_val and date_val.lower() not in ("nan", "none", ""):
            try:
                date_str = pd.to_datetime(date_val, dayfirst=False).strftime("%Y-%m-%d")
            except Exception:
                pass

        if date_str:
            # ── Transaction row (account resolved in the second pass) ─────────
            payee = ""
            if name_col is not None and name_col < len(cells):
                v = cells[name_col]
                payee = "" if v.lower() in ("nan", "none") else v

            memo = ""
            if memo_col is not None and memo_col < len(cells):
                v = cells[memo_col]
                memo = "" if v.lower() in ("nan", "none") else v

            split = ""
            if split_col is not None and split_col < len(cells):
                v = cells[split_col]
                split = "" if v.lower() in ("nan", "none") else v

            amount = None
            hash_amount = None
            if amount_col is not None and amount_col < len(cells):
                amount = _parse_amount(cells[amount_col])
            if amount is None and (debit_col is not None or credit_col is not None):
                debit  = (_parse_amount(cells[debit_col])  if debit_col  is not None and debit_col  < len(cells) else None) or 0.0
                credit = (_parse_amount(cells[credit_col]) if credit_col is not None and credit_col < len(cells) else None) or 0.0
                amount = debit_credit_amount(debit, credit)
                hash_amount = credit - debit        # pre-1.15 duplicate key

            if amount is None:
                warnings.append(f"Row {i + 2}: no amount found — skipped.")
                continue

            records.append({
                "header_account": current_account,
                "date":           date_str,
                "payee":          payee,
                "memo":           memo,
                "split":          split,
                "amount":         amount,
                "hash_amount":    amount if hash_amount is None else hash_amount,
            })
        else:
            # ── Account section header ────────────────────────────────────────
            # Date column is empty; if there's any non-empty non-total cell it's
            # an account name.
            if first_val and first_val.lower() not in ("nan", "none"):
                current_account = first_val
                section_header_seen = True

    # ── Second pass: resolve each transaction's account, then build dicts ─────
    # No section headers anywhere + a Split column present ⇒ flat export, so the
    # Split value is the account. Otherwise keep the grouped meaning (Split is the
    # offsetting account/category).
    use_split_as_account = not section_header_seen and split_col is not None

    for rec in records:
        if use_split_as_account:
            account_name = rec["split"]
            split_value  = ""            # Split was consumed as the account
        else:
            account_name = rec["header_account"]
            split_value  = rec["split"]  # offsetting account/category

        if not account_name:
            warnings.append(
                f"Transaction on {rec['date']} has no account — skipped.")
            continue

        amount = rec["amount"]
        raw = f"{account_name}|{rec['date']}|{rec['hash_amount']}|{rec['payee']}"
        import_hash = hashlib.md5(raw.encode()).hexdigest()[:16]

        transactions.append({
            "_account_name":    account_name,
            "_split":           split_value,   # QB offsetting account → category
            "date":             rec["date"],
            "payee":            rec["payee"],
            "memo":             rec["memo"],
            "amount":           str(round(amount, 2)),
            "category_id":      "",
            "class_id":         "",
            "is_transfer":      "0",
            "transfer_pair_id": "",
            "reconciled":       "0",
            "reconcile_id":     "",
            "notes":            "",
            "import_hash":      import_hash,
        })

    return transactions, warnings


def get_columns(file_bytes: bytes, filename: str) -> list[str]:
    """Return list of column names for column-mapping UI."""
    if filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls"):
        df = pd.read_excel(BytesIO(file_bytes), dtype=str, nrows=1)
    else:
        df = pd.read_csv(BytesIO(file_bytes), dtype=str, nrows=1, on_bad_lines="skip")
    return [str(c).strip() for c in df.columns]
