"""
Fixed-asset depreciation and prepaid-expense amortization. Pure math — no DB/UI.

Depreciation is NON-CASH: nothing here creates transactions. Schedules are
computed on the fly per month; reports sum the months that fall in a period.

Asset dict fields used (strings or numbers are fine):
  cost, salvage, in_service_date (YYYY-MM-DD), life_months, method,
  db_factor (declining-balance multiple, default 2.0), total_units,
  macrs_class ('3','5','7','10','15','20'), status, disposal_date,
  disposal_amount

Methods
  sl       straight-line, monthly from the in-service month
  db       declining balance at db_factor/life per year, switching to
           straight-line when that is larger, never below salvage
  units    units of production: (cost − salvage) / total_units per unit used
  expense  Section 179 / 100% bonus: the whole cost in the in-service month
  macrs    MACRS GDS, 200%/150% DB, HALF-YEAR convention, IRS Pub 946
           Table A-1. NOT implemented: mid-quarter convention (required when
           >40% of the year's property is placed in service in Q4), ADS,
           real property (27.5/39-yr) — confirm with your accountant.

Book vs tax: the books (P&L, Balance Sheet) use the asset's `method`;
`tax_method` / `tax_macrs_class` produce a separate tax schedule for reference.
"""
from datetime import date
from decimal import Decimal

from utils.money import D, cents, ZERO

METHODS = {
    "sl": "Straight-line",
    "db": "Declining balance",
    "units": "Units of production",
    "expense": "Section 179 / bonus (expense in year one)",
    "macrs": "MACRS (GDS, half-year)",
}

# IRS Publication 946 (2025), Appendix A, Table A-1: 3-, 5-, 7-, 10-, 15- and
# 20-year property, half-year convention (percent of basis per recovery year).
MACRS_HALF_YEAR = {
    "3":  ["33.33", "44.45", "14.81", "7.41"],
    "5":  ["20.00", "32.00", "19.20", "11.52", "11.52", "5.76"],
    "7":  ["14.29", "24.49", "17.49", "12.49", "8.93", "8.92", "8.93", "4.46"],
    "10": ["10.00", "18.00", "14.40", "11.52", "9.22", "7.37", "6.55", "6.55", "6.56",
           "6.55", "3.28"],
    "15": ["5.00", "9.50", "8.55", "7.70", "6.93", "6.23", "5.90", "5.90", "5.91", "5.90",
           "5.91", "5.90", "5.91", "5.90", "5.91", "2.95"],
    "20": ["3.750", "7.219", "6.677", "6.177", "5.713", "5.285", "4.888", "4.522", "4.462",
           "4.461", "4.462", "4.461", "4.462", "4.461", "4.462", "4.461", "4.462", "4.461",
           "4.462", "4.461", "2.231"],
}
MACRS_EXAMPLES = {
    "3": "3-year (tractor units, some tools)", "5": "5-year (computers, vehicles, office equipment)",
    "7": "7-year (furniture, fixtures, most machinery)", "10": "10-year (vessels, some farm structures)",
    "15": "15-year (land improvements)", "20": "20-year (farm buildings)",
}


def _ym(iso: str) -> tuple[int, int]:
    d = date.fromisoformat(iso)
    return d.year, d.month


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def _spread(total: Decimal, months: list[tuple[int, int]]) -> list[tuple[int, int, Decimal]]:
    """Split an amount evenly over months, cent-exact (remainder to the last)."""
    if not months:
        return []
    each = cents(total / len(months))
    out = [(y, m, each) for y, m in months]
    diff = cents(total) - each * len(months)
    if diff:
        y, m, a = out[-1]
        out[-1] = (y, m, a + diff)
    return out


def _end_ym(asset) -> tuple[int, int] | None:
    """Last month depreciated when the asset was sold/disposed (its disposal month)."""
    if (asset.get("status") in ("sold", "disposed")) and asset.get("disposal_date"):
        return _ym(asset["disposal_date"])
    return None


def monthly_schedule(asset: dict, usage=(), method: str | None = None,
                     macrs_class: str | None = None) -> list[tuple[int, int, Decimal]]:
    """[(year, month, amount)] for the whole life of the asset."""
    method = method or asset.get("method") or "sl"
    if not asset.get("in_service_date"):
        return []
    cost = D(asset.get("cost"))
    salvage = D(asset.get("salvage"))
    y0, m0 = _ym(asset["in_service_date"])
    life = int(D(asset.get("life_months")) or 0)
    end = _end_ym(asset)

    if method == "expense":
        sched = [(y0, m0, cents(cost))]
    elif method == "sl":
        if life <= 0:
            return []
        months = [_add_months(y0, m0, i) for i in range(life)]
        sched = _spread(cost - salvage, months)
    elif method == "db":
        sched = _declining_balance(cost, salvage, life, D(asset.get("db_factor") or 2), y0, m0)
    elif method == "units":
        total_units = D(asset.get("total_units"))
        if total_units <= 0:
            return []
        per_unit = (cost - salvage) / total_units
        remaining = cents(cost - salvage)
        by_month: dict = {}
        for u in sorted(usage, key=lambda x: x.get("date", "")):
            if not u.get("date"):
                continue
            amt = min(cents(D(u.get("units")) * per_unit), remaining)
            if amt <= 0:
                continue
            remaining -= amt
            key = _ym(u["date"])
            by_month[key] = by_month.get(key, ZERO) + amt
        sched = [(y, m, a) for (y, m), a in sorted(by_month.items())]
    elif method == "macrs":
        sched = _macrs(cost, macrs_class or asset.get("macrs_class") or "5", y0, m0, end)
        end = None   # disposal already applied (half-year in the disposal year)
    else:
        return []

    if end:
        sched = [(y, m, a) for y, m, a in sched if (y, m) <= end]
    return sched


def _declining_balance(cost, salvage, life, factor, y0, m0):
    if life <= 0:
        return []
    life_years = Decimal(life) / 12
    rate = factor / life_years
    nbv = cost
    sched = []
    y, m = y0, m0
    months_left = life
    while months_left > 0 and nbv - salvage > Decimal("0.005"):
        months_this_year = min(13 - m, months_left)
        frac = Decimal(months_this_year) / 12
        db_amt = nbv * rate * frac
        sl_amt = (nbv - salvage) * Decimal(months_this_year) / Decimal(months_left)
        amt = min(max(db_amt, sl_amt), nbv - salvage)
        amt = cents(amt)
        months = [(y, mm) for mm in range(m, m + months_this_year)]
        sched.extend(_spread(amt, months))
        nbv -= amt
        months_left -= months_this_year
        y, m = y + 1, 1
    return sched


def _macrs(cost, cls, y0, m0, end):
    pcts = MACRS_HALF_YEAR.get(str(cls))
    if not pcts:
        return []
    sched = []
    for k, pct in enumerate(pcts):
        year = y0 + k
        amt = cents(cost * D(pct) / 100)
        if end and year > end[0]:
            break
        if end and year == end[0] and k > 0:
            amt = cents(amt / 2)    # half-year convention in the year of disposition
        months = [(year, mm) for mm in range(m0 if k == 0 else 1, 13)]
        sched.extend(_spread(amt, months))
    return sched


def _in_range(y, m, start, end) -> bool:
    key = f"{y:04d}-{m:02d}"
    return (not start or key >= start[:7]) and (not end or key <= end[:7])


def depreciation_between(asset: dict, start: str = "", end: str = "", usage=(),
                         method=None, macrs_class=None) -> Decimal:
    """Depreciation for months overlapping [start, end] (whole months)."""
    return cents(sum((a for y, m, a in monthly_schedule(asset, usage, method, macrs_class)
                      if _in_range(y, m, start, end)), ZERO))


def accumulated(asset: dict, as_of: str, usage=(), method=None, macrs_class=None) -> Decimal:
    return depreciation_between(asset, "", as_of, usage, method, macrs_class)


def net_book_value(asset: dict, as_of: str, usage=()) -> Decimal:
    """Cost less accumulated depreciation; 0 once sold/disposed (on/after that date)."""
    if not owned_on(asset, as_of):
        return ZERO
    return cents(D(asset.get("cost")) - accumulated(asset, as_of, usage))


def _disposed_by(asset, as_of) -> bool:
    return (asset.get("status") in ("sold", "disposed") and asset.get("disposal_date")
            and asset["disposal_date"] <= as_of)


def owned_on(asset: dict, as_of: str) -> bool:
    acquired = asset.get("purchase_date") or asset.get("in_service_date") or ""
    return bool(acquired) and acquired <= as_of and not _disposed_by(asset, as_of)


def gain_on_disposal(asset: dict, usage=()) -> Decimal:
    """Proceeds − net book value at disposal (book basis). 0 if not disposed."""
    if asset.get("status") not in ("sold", "disposed") or not asset.get("disposal_date"):
        return ZERO
    nbv = cents(D(asset.get("cost")) - accumulated(asset, asset["disposal_date"], usage))
    return cents(D(asset.get("disposal_amount")) - nbv)


def annual_schedule(asset: dict, usage=(), method=None, macrs_class=None) -> list[dict]:
    """[{year, depreciation, accumulated, book_value}] for display."""
    by_year: dict = {}
    for y, m, a in monthly_schedule(asset, usage, method, macrs_class):
        by_year[y] = by_year.get(y, ZERO) + a
    out = []
    acc = ZERO
    cost = D(asset.get("cost"))
    for y in sorted(by_year):
        acc += by_year[y]
        out.append({"year": y, "depreciation": cents(by_year[y]), "accumulated": cents(acc),
                    "book_value": cents(cost - acc)})
    return out


def portfolio(assets, as_of: str, start: str = "", usage_by_asset=None) -> dict:
    """
    Totals across assets for reports:
      cost, accumulated, nbv           (owned on as_of)
      dep_in_range                     depreciation for [start, as_of]
      dep_before                       depreciation before start (all assets)
      gain_in_range / gain_before      disposal gains (losses negative)
      by_category                      {expense_category_id: dep_in_range}
    Disposed assets drop off the balance sheet but their depreciation and
    gain stay in the P&L of the periods they belong to.
    """
    usage_by_asset = usage_by_asset or {}
    cost = acc = nbv = dep_in = dep_before = gain_in = gain_before = ZERO
    by_cat: dict = {}
    for a in assets:
        u = usage_by_asset.get(a.get("id"), ())
        if owned_on(a, as_of):
            c = D(a.get("cost"))
            ad = accumulated(a, as_of, u)
            cost += c
            acc += ad
            nbv += c - ad
        d_in = depreciation_between(a, start, as_of, u) if start else accumulated(a, as_of, u)
        d_before = depreciation_between(a, "", _prev_month_end(start), u) if start else ZERO
        dep_in += d_in
        dep_before += d_before
        cat = a.get("expense_category_id") or ""
        by_cat[cat] = by_cat.get(cat, ZERO) + d_in
        if a.get("status") in ("sold", "disposed") and a.get("disposal_date") \
                and a["disposal_date"] <= as_of:
            g = gain_on_disposal(a, u)
            if start and a["disposal_date"] < start:
                gain_before += g
            else:
                gain_in += g
    return {k: cents(v) for k, v in (("cost", cost), ("accumulated", acc), ("nbv", nbv),
                                     ("dep_in_range", dep_in), ("dep_before", dep_before),
                                     ("gain_in_range", gain_in), ("gain_before", gain_before))} | \
        {"by_category": {k: cents(v) for k, v in by_cat.items() if v}}


def _prev_month_end(iso: str) -> str:
    if not iso:
        return ""
    y, m = _ym(iso)
    py, pm = _add_months(y, m, -1)
    return f"{py:04d}-{pm:02d}-28"
