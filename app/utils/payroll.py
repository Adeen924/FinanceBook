"""
Payroll ESTIMATES — pure math, no DB/UI. Not a substitute for a payroll
provider or accountant.

Federal income tax withholding follows IRS Pub 15-T Worksheet 1A (percentage
method for automated payroll systems) for a Form W-4 from 2020 or later with
the Step 2 box NOT checked. Step-2-checkbox and pre-2020 (allowance) W-4s are
not supported — compute_paycheck() flags them instead of guessing.

Tax figures come from app/data/tax_tables_<year>.json (see load_tables()).
"""
import json
import os
from decimal import Decimal

from utils.money import D, cents, ZERO

PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12, "annual": 1}
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def available_years() -> list[int]:
    try:
        names = os.listdir(_DATA_DIR)
    except OSError:
        return []
    return sorted(int(n[11:15]) for n in names
                  if n.startswith("tax_tables_") and n.endswith(".json") and n[11:15].isdigit())


def load_tables(year: int | None = None) -> dict:
    """Tables for `year`, or the latest year on file at or before it."""
    years = available_years()
    if not years:
        raise FileNotFoundError("No tax_tables_<year>.json in app/data")
    pick = max([y for y in years if year is None or y <= year] or [min(years)])
    with open(os.path.join(_DATA_DIR, f"tax_tables_{pick}.json"), encoding="utf-8") as f:
        t = json.load(f)
    t["_requested_year"] = year
    t["_stale"] = year is not None and pick != year
    return t


def federal_withholding(wages, periods: int, w4: dict, tables: dict) -> Decimal:
    """
    Pub 15-T Worksheet 1A. w4: {filing_status 'single'|'mfj'|'hoh', step3 (annual credits),
    step4a (other income), step4b (deductions), step4c (extra per period)}.
    """
    wt = tables["withholding_pub15t"]
    status = w4.get("filing_status") or "single"
    rows = wt["standard"].get(status) or wt["standard"]["single"]
    annual = D(wages) * periods + D(w4.get("step4a"))                       # 1c + 1d
    adj = D(w4.get("step4b")) + D(wt["step2_unchecked_adjustment"].get(status, 8600))
    taxable = max(annual - adj, ZERO)                                       # 1i
    tax = ZERO
    for lo, hi, base, rate, over in rows:
        if taxable >= D(lo) and (hi is None or taxable < D(hi)):
            tax = D(base) + (taxable - D(over)) * D(rate)                   # 2f
            break
    per_period = tax / periods                                              # 2h
    per_period -= D(w4.get("step3")) / periods                              # 3a–3c
    per_period = max(per_period, ZERO) + D(w4.get("step4c"))                # 4b
    return cents(per_period)


def fica(gross, ytd_gross, tables: dict) -> dict:
    """Employee and employer Social Security / Medicare, honoring the wage base."""
    gross, ytd = D(gross), D(ytd_gross)
    ss = tables["social_security"]
    md = tables["medicare"]
    ss_wages = max(min(gross, D(ss["wage_base"]) - ytd), ZERO)
    thresh = D(md["additional_withholding_threshold"])
    addl_wages = max(ytd + gross - max(thresh, ytd), ZERO) if ytd + gross > thresh else ZERO
    return {"ss_ee": cents(ss_wages * D(ss["employee_rate"])),
            "ss_er": cents(ss_wages * D(ss["employer_rate"])),
            "med_ee": cents(gross * D(md["employee_rate"])),
            "med_er": cents(gross * D(md["employer_rate"])),
            "addl_med": cents(addl_wages * D(md["additional_rate"]))}


def capped_tax(gross, ytd_gross, base, rate) -> Decimal:
    taxable = max(min(D(gross), D(base) - D(ytd_gross)), ZERO)
    return cents(taxable * D(rate))


def compute_paycheck(emp: dict, gross, ytd_gross, tables: dict,
                     suta_rate=0, suta_base=0, state_code: str = "") -> dict:
    """
    One paycheck. emp: {pay_frequency, filing_status, w4_* fields, state_rate (% of gross),
    w4_step2 (0/1), w4_pre2020 (0/1)}. Returns withholdings, employer taxes, net, warnings.
    """
    gross = cents(gross)
    periods = PERIODS.get(emp.get("pay_frequency") or "biweekly", 26)
    warnings = []
    w4 = {"filing_status": emp.get("filing_status") or "single",
          "step3": emp.get("w4_dependents") or 0, "step4a": emp.get("w4_other_income") or 0,
          "step4b": emp.get("w4_deductions") or 0, "step4c": emp.get("w4_extra") or 0}
    if str(emp.get("w4_step2") or "0") == "1" or str(emp.get("w4_pre2020") or "0") == "1":
        fit = cents(D(w4["step4c"]))
        warnings.append("Federal withholding not estimated: Step 2 checkbox / pre-2020 W-4 tables "
                        "aren't loaded. Enter it from your payroll provider.")
    else:
        fit = federal_withholding(gross, periods, w4, tables)
    f = fica(gross, ytd_gross, tables)
    state = cents(gross * D(emp.get("state_rate")) / 100)
    futa_rate = D(tables["futa"]["net_rate_after_credit"]) + D(
        tables["futa"]["credit_reduction_states"].get(state_code, 0))
    futa = capped_tax(gross, ytd_gross, tables["futa"]["wage_base"], futa_rate)
    suta = capped_tax(gross, ytd_gross, suta_base, D(suta_rate) / 100) if D(suta_base) > 0 else \
        cents(gross * D(suta_rate) / 100)
    withheld = fit + f["ss_ee"] + f["med_ee"] + f["addl_med"] + state
    employer = f["ss_er"] + f["med_er"] + futa + suta
    if withheld > gross:
        warnings.append("Withholding is more than gross pay — check the W-4 entries.")
    return {"gross": gross, "fit": fit, "ss_ee": f["ss_ee"], "med_ee": f["med_ee"],
            "addl_med": f["addl_med"], "state": state, "withheld": cents(withheld),
            "net": cents(gross - withheld), "ss_er": f["ss_er"], "med_er": f["med_er"],
            "futa": futa, "suta": suta, "employer_tax": cents(employer), "warnings": warnings}


def gross_for_period(emp: dict, hours) -> Decimal:
    if (emp.get("pay_type") or "hourly") == "salary":
        return cents(D(emp.get("pay_rate")) / PERIODS.get(emp.get("pay_frequency") or "biweekly", 26))
    return cents(D(emp.get("pay_rate")) * D(hours))


def nec_threshold(tables: dict, year: int) -> Decimal:
    by_year = tables.get("form_1099", {}).get("nec_threshold_by_year", {})
    known = sorted(int(y) for y in by_year)
    pick = max([y for y in known if y <= year] or known or [2026])
    return D(by_year.get(str(pick), 600))
