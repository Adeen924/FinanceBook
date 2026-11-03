"""
Small-business tax helpers — ESTIMATES for planning, not tax advice. Pure.

  mileage_rate()         IRS business rate for a date (date-ranged; 2026 changed in July)
  home_office()          simplified ($/sq ft, capped) and actual-expense methods
  estimated_tax()        rough annual federal income + self-employment tax from
                         year-to-date Schedule C profit, and the quarterly amount
  next_due_dates()       upcoming Form 1040-ES due dates
"""
import calendar
from datetime import date

from utils.money import D, cents, ZERO


def mileage_rate(tables: dict, on: str):
    for r in tables.get("mileage", {}).get("business_rates", []):
        if r["from"] <= on <= r["to"]:
            return D(r["rate"])
    rates = tables.get("mileage", {}).get("business_rates", [])
    return D(rates[-1]["rate"]) if rates else ZERO


def home_office(tables: dict, office_sqft, home_sqft=0, home_expenses=0,
                gross_income_limit=None) -> dict:
    """Both methods side by side; the limit is the business's gross income from the home."""
    ho = tables["home_office_simplified"]
    sq = min(D(office_sqft), D(ho["max_sqft"]))
    simplified = cents(sq * D(ho["rate_per_sqft"]))
    pct = (D(office_sqft) / D(home_sqft)) if D(home_sqft) > 0 else ZERO
    actual = cents(D(home_expenses) * pct)
    if gross_income_limit is not None:
        simplified = min(simplified, max(cents(gross_income_limit), ZERO))
        actual = min(actual, max(cents(gross_income_limit), ZERO))
    return {"simplified": simplified, "actual": actual, "business_pct": pct,
            "better": "actual" if actual > simplified else "simplified"}


def _bracket_tax(taxable, brackets) -> object:
    tax = ZERO
    taxable = D(taxable)
    for i, (lo, rate) in enumerate(brackets):
        hi = D(brackets[i + 1][0]) if i + 1 < len(brackets) else None
        if taxable <= D(lo):
            break
        top = taxable if hi is None else min(taxable, hi)
        tax += (top - D(lo)) * D(rate)
    return cents(tax)


def estimated_tax(tables: dict, ytd_profit, as_of: str, filing_status: str = "single",
                  other_income=0, w2_wages=0) -> dict:
    """
    Annualize year-to-date net profit, then estimate:
      SE tax = 92.35% × profit × 15.3% (Social Security part capped at the
               wage base, less any W-2 wages), deductible half
      income tax on (profit + other income − ½ SE tax − standard deduction −
               QBI 20%) using the year's brackets
    Ignores credits, other deductions, state tax, and the QBI phase-out above
    the threshold (flagged). For planning only.
    """
    d = date.fromisoformat(as_of)
    # Completed months so far (July 1 → 6.0), never less than one month.
    days_in_month = calendar.monthrange(d.year, d.month)[1]
    months = max(d.month - 1 + (d.day - 1) / days_in_month, 1)
    projected = cents(D(ytd_profit) * D(12) / D(str(round(months, 6))))
    se = tables["self_employment"]
    ss_base = D(tables["social_security"]["wage_base"])
    net_se = max(projected * D(se["net_earnings_factor"]), ZERO)
    se_tax = ZERO
    if net_se >= D(se["minimum_net_earnings"]):
        ss_part = min(net_se, max(ss_base - D(w2_wages), ZERO)) * D(se["social_security_part"])
        se_tax = cents(ss_part + net_se * D(se["medicare_part"]))
    half_se = cents(se_tax * D(se["deduction_share"]))
    it = tables["income_tax"]
    std = D(it["standard_deduction"].get(filing_status, it["standard_deduction"]["single"]))
    qbi_base = max(projected - half_se, ZERO)
    qbi = cents(qbi_base * D(it["qbi_rate"]))
    agi = projected + D(other_income) + D(w2_wages) - half_se
    taxable = max(agi - std - qbi, ZERO)
    income_tax = _bracket_tax(taxable, it["brackets"].get(filing_status, it["brackets"]["single"]))
    total = cents(income_tax + se_tax)
    warnings = []
    if taxable > D(it["qbi_threshold"].get(filing_status, 201750)):
        warnings.append("Income is above the QBI threshold — the 20% deduction may be limited.")
    return {"projected_profit": projected, "se_tax": se_tax, "income_tax": income_tax,
            "total": total, "quarterly": cents(total / 4), "qbi": qbi,
            "standard_deduction": std, "taxable_income": cents(taxable), "warnings": warnings}


def next_due_dates(tables: dict, today: str) -> list[str]:
    return [d for d in tables.get("estimated_tax_due_dates", {}).get("dates", []) if d >= today]
