"""
Parse CSV and XLSX bank export files.
Attempts to auto-detect columns; falls back to manual mapping.
"""
import csv
import hashlib
import re
import pandas as pd
from io import BytesIO, StringIO


COMMON_DATE_COLS = ["date", "transaction date", "posted date", "trans date", "trans. date", "posting date"]
COMMON_PAYEE_COLS = ["payee", "name", "description", "transaction description", "details", "merchant"]
COMMON_MEMO_COLS = ["memo", "notes", "note"]
COMMON_SPLIT_COLS = ["split"]
COMMON_AMOUNT_COLS = ["amount", "transaction amount", "debit/credit"]
COMMON_DEBIT_COLS = ["debit", "withdrawal", "withdrawals", "charges"]
COMMON_CREDIT_COLS = ["credit", "deposit", "deposits", "payments"]


def _normalize(s: str) -> str:
    return str(s).lower().strip()


def _find_col(df_cols: list[str], candidates: list[str]) -> str | None:
    norm_cols = {_normalize(c): c for c in df_cols}
    for cand in candidates:
        if _normalize(cand) in norm_cols:
            return norm_cols[_normalize(cand)]
    return None


def _detect_header_row(raw) -> int | None:
    """
    Index of the row that looks like the column-header row — i.e. the first row
    (within the first 25) that contains a recognizable Date column. Lets us skip
    the title/company/report-name rows many bank & QuickBooks exports put on top.
    """
    for i in range(min(25, len(raw))):
        cells = [_normalize(c) for c in raw.iloc[i].tolist()]
        cells = [c for c in cells if c and c != "nan"]
        if cells and any(c in COMMON_DATE_COLS for c in cells):
            return i
    return None


def _parse_amount(val) -> float | None:
    if pd.isna(val) or str(val).strip().lower() in ("", "-", "n/a", "nan", "none"):
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


def parse_spreadsheet(file_bytes: bytes, filename: str, account_id: str,
                      col_mapping: dict = None,
                      reverse_signs: bool = False) -> tuple[list[dict], list[str]]:
    """
    Parse CSV or XLSX.
    Returns (transactions, warnings).
    col_mapping can override auto-detection: {"date": "Date", "payee": "Payee", ...}
    reverse_signs: the file's single Amount column has money out as positive
    (common for credit-card exports, where a charge is "42.10") — negate it.
    Debit / Credit columns are never reversed: their meaning is fixed.
    """
    warnings = []

    # Read the file as a raw grid (no header) so we can locate the real header
    # row even when title/preamble rows sit on top (common in bank & QB exports).
    if filename.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(BytesIO(file_bytes), dtype=str, header=None)
    else:
        # Use the csv module: pandas' header=None keys the column count off the
        # first line, so a short title row on top would make it drop the wider
        # header/data rows. Reading rows ourselves and padding avoids that.
        text = file_bytes.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(StringIO(text)))
        width = max((len(r) for r in rows), default=0)
        rows = [r + [""] * (width - len(r)) for r in rows]
        raw = pd.DataFrame(rows, dtype=str)

    if raw.empty:
        return [], ["The file appears to be empty."]

    header_idx = _detect_header_row(raw)
    if header_idx is None:
        header_idx = 0  # fall back to treating the first row as the header

    # Re-frame the table using the detected header row.
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[header_idx].tolist()]
    df = df.reset_index(drop=True)

    if col_mapping:
        date_col   = col_mapping.get("date")
        payee_col  = col_mapping.get("payee")
        amount_col = col_mapping.get("amount")
        debit_col  = col_mapping.get("debit")
        credit_col = col_mapping.get("credit")
        memo_col   = col_mapping.get("memo")
        split_col  = col_mapping.get("split")
    else:
        date_col   = _find_col(df.columns, COMMON_DATE_COLS)
        payee_col  = _find_col(df.columns, COMMON_PAYEE_COLS)
        amount_col = _find_col(df.columns, COMMON_AMOUNT_COLS)
        debit_col  = _find_col(df.columns, COMMON_DEBIT_COLS)
        credit_col = _find_col(df.columns, COMMON_CREDIT_COLS)
        memo_col   = _find_col(df.columns, COMMON_MEMO_COLS)
        split_col  = _find_col(df.columns, COMMON_SPLIT_COLS)

    # A "Split" column marks a QuickBooks export. Those are handled by the
    # dedicated Import Wizard, which understands both shapes — grouped
    # "Transaction Detail by Account" reports (account = section header, Split =
    # offsetting category) and flat exports (account = the Split column). Send the
    # user there rather than importing everything into one account here.
    if split_col:
        raise ValueError(
            "This looks like a QuickBooks export (it has a 'Split' column).\n\n"
            "Import it from Settings → Open Import Wizard → Step 2 "
            "(Transactions), which understands the Split column — including "
            "using it as the account when the file isn't grouped by account.")

    if not date_col:
        warnings.append("Could not detect a Date column.")
    if not (payee_col or memo_col or split_col):
        warnings.append("Could not detect a Description/Payee column.")
    if not amount_col and not (debit_col or credit_col):
        warnings.append("Could not detect an Amount column.")

    results = []
    for idx, row in df.iterrows():
        date_val = str(row.get(date_col, "") if date_col else "").strip()
        if not date_val or date_val.lower() in ("nan", ""):
            continue

        # Normalize date
        try:
            date_str = pd.to_datetime(date_val, dayfirst=False).strftime("%Y-%m-%d")
        except Exception:
            warnings.append(f"Row {header_idx+idx+2}: unrecognized date '{date_val}', skipped.")
            continue

        # Resolve payee / memo. Prefer a real payee/description column; fall back
        # to the memo when that's all the file has (files carrying a QB-style
        # "Split" column are redirected to the wizard before we reach here).
        payee = str(row.get(payee_col, "") if payee_col else "").strip()
        memo  = str(row.get(memo_col,  "") if memo_col  else "").strip()
        if payee.lower() in ("nan", "none"): payee = ""
        if memo.lower()  in ("nan", "none"): memo  = ""
        if not payee:
            payee, memo = memo, ""

        # Resolve amount
        amount = None
        hash_amount = None
        if amount_col:
            amount = _parse_amount(row.get(amount_col, ""))
            if amount is not None and reverse_signs:
                amount = -amount
        elif debit_col or credit_col:
            debit = _parse_amount(row.get(debit_col, "") if debit_col else None) or 0.0
            credit = _parse_amount(row.get(credit_col, "") if credit_col else None) or 0.0
            amount = debit_credit_amount(debit, credit)
            # The duplicate check hashes the amount as versions before 1.15
            # computed it (credit - debit as written), so re-importing a file
            # imported before the sign fix doesn't add every row a second time.
            hash_amount = credit - debit

        if amount is None:
            warnings.append(f"Row {header_idx+idx+2}: no amount found, skipped.")
            continue

        raw = f"{account_id}|{date_str}|{amount if hash_amount is None else hash_amount}|{payee}"
        import_hash = hashlib.md5(raw.encode()).hexdigest()[:16]

        results.append({
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
        })

    return results, warnings


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
