"""
Loan amortization helpers.
All functions are pure — no DB or UI imports.
"""
import calendar
from datetime import date


# ── date helpers ──────────────────────────────────────────────────────────────

def add_months(d: date, months: int) -> date:
    """Add a number of months to a date, clamping the day to month-end."""
    month = d.month - 1 + months
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ── core calculations ─────────────────────────────────────────────────────────

def calc_monthly_payment(principal: float, annual_rate: float, term_months: int) -> float:
    """Standard amortizing monthly payment (PMT formula)."""
    if principal <= 0 or term_months <= 0:
        return 0.0
    if annual_rate <= 0:
        return round(principal / term_months, 2)
    r = annual_rate / 12
    n = term_months
    return round(principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1), 2)


def amortization_schedule(loan: dict) -> list[dict]:
    """
    Generate the complete amortization schedule for a loan dict.

    Expected loan keys:
        original_principal, annual_rate, term_months,
        start_date (YYYY-MM-DD, first payment date),
        payment_amount (0 → auto-calculate)

    Returns list of dicts:
        payment_number, due_date, payment, principal, interest,
        balance (remaining after this payment)
    """
    principal   = float(loan.get("original_principal") or 0)
    annual_rate = float(loan.get("annual_rate") or 0)
    term_months = int(loan.get("term_months") or 0)
    stored_pmt  = float(loan.get("payment_amount") or 0)

    try:
        first_date = date.fromisoformat(str(loan.get("start_date") or ""))
    except (ValueError, TypeError):
        first_date = date.today()

    r   = annual_rate / 12
    pmt = stored_pmt or calc_monthly_payment(principal, annual_rate, term_months)

    schedule = []
    balance  = principal

    for n in range(1, term_months + 1):
        if balance <= 0.005:
            break

        interest     = round(balance * r, 2)
        actual_pmt   = round(min(pmt, balance + interest), 2)
        principal_pmt = round(actual_pmt - interest, 2)
        balance      = round(max(balance - principal_pmt, 0.0), 2)
        due_date     = add_months(first_date, n - 1)

        schedule.append({
            "payment_number": n,
            "due_date":       due_date.isoformat(),
            "payment":        actual_pmt,
            "principal":      principal_pmt,
            "interest":       interest,
            "balance":        balance,
        })

    return schedule


def split_for_date(loan: dict, txn_date: str, actual_amount: float) -> dict:
    """
    Given a transaction date and the actual dollar amount paid, return the
    principal/interest split and which payment number this is.

    If actual_amount > scheduled payment the extra reduces principal.

    Returns dict:
        payment_number, scheduled_payment, interest, principal,
        balance_before, balance_after
    """
    try:
        txn_d = date.fromisoformat(str(txn_date))
    except (ValueError, TypeError):
        txn_d = date.today()

    try:
        first_d = date.fromisoformat(str(loan.get("start_date") or ""))
    except (ValueError, TypeError):
        first_d = txn_d

    # Month offset from first payment date to transaction date
    months_elapsed = (txn_d.year * 12 + txn_d.month) - (first_d.year * 12 + first_d.month)
    payment_num    = max(1, months_elapsed + 1)

    schedule = amortization_schedule(loan)
    if not schedule:
        return {
            "payment_number":   payment_num,
            "scheduled_payment": actual_amount,
            "interest":         0.0,
            "principal":        actual_amount,
            "balance_before":   0.0,
            "balance_after":    0.0,
        }

    # Clamp to the last scheduled payment if the date is past the term
    idx   = min(payment_num - 1, len(schedule) - 1)
    entry = schedule[idx]

    original  = float(loan.get("original_principal") or 0)
    balance_before = schedule[idx - 1]["balance"] if idx > 0 else original

    r        = float(loan.get("annual_rate") or 0) / 12
    interest = round(balance_before * r, 2)
    principal = round(max(0.0, actual_amount - interest), 2)

    return {
        "payment_number":    idx + 1,
        "scheduled_payment": entry["payment"],
        "interest":          interest,
        "principal":         principal,
        "balance_before":    balance_before,
        "balance_after":     round(max(0.0, balance_before - principal), 2),
    }


def remaining_balance(loan: dict, paid_transactions: list[dict]) -> float:
    """
    Remaining loan balance = original_principal minus all principal_amount values
    recorded on linked transactions.
    """
    original = float(loan.get("original_principal") or 0)
    paid     = sum(float(t.get("principal_amount") or 0) for t in paid_transactions)
    return max(0.0, original - paid)
