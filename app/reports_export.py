"""
Year-end package: one folder of CSV files for your accountant. No Qt.

export_year_end(db, year, folder) → list of written file paths.
"""
import csv
import os
from datetime import date

from utils.money import D, cents, fmt


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return path


def _money(v) -> str:
    return f"{cents(v):.2f}"


def pnl_csv_rows(rows) -> list:
    """P&L lines in report order: Income, Expense, then (when there is any) Net
    operating income and the Other Income / Other Expense section, then Net
    income. rows are utils.reports.pnl_rows() rows."""
    from utils.reports import pivot, pnl_section_label, PNL_SECTION_ORDER
    g = pivot(rows, lambda r: (pnl_section_label(r), r["group"]), lambda r: "total")
    has_other = any(r.get("other") for r in rows)
    out = []
    for sec in PNL_SECTION_ORDER:
        if sec == "Other Income" and has_other:
            op = sum((r["amount"] for r in rows if not r.get("other")), D(0))
            out.append(["", "Net operating income", _money(op)])
        for k in sorted(k for k in g if k != "__total__" and k[0] == sec):
            out.append([sec, k[1], _money(g[k]["total"])])
    out.append(["", "Net income", _money(g.get("__total__", {}).get("__total__", 0))])
    return out


def export_year_end(db, year: int, folder: str) -> list[str]:
    from utils.reports import pivot, months_between, schedule_c, cash_flow
    from utils.ledger import balance_sheet, owner_investment
    from utils import depreciation as dep
    from utils.inventory import run_costing
    from utils.invoicing import aging, AGING_BUCKETS
    from business_categories import TAX_LINES

    start, end = f"{year}-01-01", f"{year}-12-31"
    out_dir = os.path.join(folder, f"FinanceBook year-end {year}")
    os.makedirs(out_dir, exist_ok=True)
    files = []
    p = lambda name: os.path.join(out_dir, name)  # noqa: E731
    cats = {c["id"]: c for c in db.get_categories()}
    accounts = db.get_accounts(active=None)
    acct_names = {a["id"]: a["name"] for a in accounts}
    rows = db.report_rows(start, end)

    files.append(_write(p(f"profit_and_loss_{year}.csv"), ["Section", "Account", "Amount"],
                        pnl_csv_rows(rows)))

    months = months_between(start, end)
    gm = pivot(rows, lambda r: r["group"], lambda r: r["month"])
    files.append(_write(p(f"profit_and_loss_by_month_{year}.csv"), ["Category"] + months + ["Total"],
                        [[k] + [_money(v.get(m, 0)) for m in months] + [_money(v["__total__"])]
                         for k, v in sorted(gm.items()) if k != "__total__"]))

    bs = balance_sheet(accounts, db.get_transactions(end=end), cats, end, start,
                       **db.balance_sheet_extras(end, start))
    bs_rows = []
    for section, key, tot in (("Assets", "assets", "total_assets"),
                              ("Liabilities", "liabilities", "total_liabilities"),
                              ("Equity", "equity", "total_equity")):
        bs_rows += [[section, n, _money(v)] for n, v, _ in bs[key]]
        bs_rows.append([section, f"Total {section}", _money(bs[tot])])
    files.append(_write(p(f"balance_sheet_{end}.csv"), ["Section", "Line", "Amount"], bs_rows))

    sc = schedule_c(rows, cats)
    sc_rows = [[TAX_LINES.get(k, f"Line {k}"), _money(v["book"]), _money(v["deductible"])]
               for k, v in sorted(sc["lines"].items(), key=lambda kv: _line_sort(kv[0]))]
    sc_rows += [["Gross income", "", _money(sc["gross_income"])],
                ["Total expenses", "", _money(sc["expenses"])],
                ["Net profit (estimate)", "", _money(sc["net_profit"])],
                ["Not mapped to a Schedule C line (review!)", "", _money(sc["unmapped"])]]
    files.append(_write(p(f"schedule_c_summary_{year}.csv"),
                        ["Schedule C line", "Book amount", "Deductible amount"], sc_rows))

    cf = cash_flow(accounts, db.get_transactions(), cats, start, end)
    cf_rows = [["Beginning cash", "", _money(cf["begin"])]]
    for sec in ("operating", "investing", "financing"):
        cf_rows += [[sec.title(), label, _money(v)] for label, v in cf["sections"][sec].items()]
        cf_rows.append([sec.title(), "Net", _money(cf["totals"][sec])])
    cf_rows.append(["Ending cash", "", _money(cf["end"])])
    files.append(_write(p(f"cash_flow_{year}.csv"), ["Section", "Line", "Amount"], cf_rows))

    usage = db.usage_by_asset()
    files.append(_write(
        p(f"depreciation_schedule_{year}.csv"),
        ["Asset", "Class", "In service", "Cost", "Book method", f"Book depreciation {year}",
         "Accumulated (book)", "Net book value", "Tax method", f"Tax depreciation {year} (reference)",
         "Status", "Disposed", "Proceeds", "Gain/(loss)"],
        [[a["name"], a.get("asset_class", ""), a.get("in_service_date", ""), _money(a.get("cost")),
          dep.METHODS.get(a.get("method"), ""),
          _money(dep.depreciation_between(a, start, end, usage.get(a["id"], []))),
          _money(dep.accumulated(a, end, usage.get(a["id"], []))),
          _money(dep.net_book_value(a, end, usage.get(a["id"], []))),
          dep.METHODS.get(a.get("tax_method"), ""),
          _money(dep.depreciation_between(a, start, end, usage.get(a["id"], []),
                                          method=a.get("tax_method") or "macrs",
                                          macrs_class=a.get("tax_macrs_class") or "5")),
          a.get("status", ""), a.get("disposal_date", ""), _money(a.get("disposal_amount")),
          _money(dep.gain_on_disposal(a, usage.get(a["id"], [])))]
         for a in db.get_assets()]))

    files.append(_write(
        p(f"1099_summary_{year}.csv"),
        ["Contractor", "Company", "Tax ID on file", "Address", f"Paid in {year}", "Needs 1099-NEC"],
        [[r["contact"]["name"], r["contact"].get("company", ""),
          "yes" if not r["missing_tax_id"] else "MISSING", r["contact"].get("address", "").replace("\n", ", "),
          _money(r["paid"]), "yes" if r["needs_1099"] else "no"] for r in db.contractor_payments(year)]))

    st = db.sales_tax_summary(start, end)
    files.append(_write(p(f"sales_tax_{year}.csv"), ["Month", "Collected", "Remitted"],
                        [[r["month"], _money(r["collected"]), _money(r["remitted"])] for r in st["rows"]]
                        + [["Total", _money(st["collected"]), _money(st["remitted"])],
                           ["Owed at year end", _money(st["owed"]), ""]]))

    files.append(_write(p(f"mileage_log_{year}.csv"),
                        ["Date", "From", "To", "Purpose", "Miles", "Rate", "Deduction", "Vehicle"],
                        [[m["date"], m["from_loc"], m["to_loc"], m["purpose"], f"{m['miles']:g}",
                          f"{m['rate']}", _money(m["miles"] * m["rate"]), m["vehicle"]]
                         for m in sorted(db.get_mileage(start, end), key=lambda x: x["date"])]))

    oi = owner_investment(accounts, db.get_transactions(end=end), start, end, db.owner_person_id())
    files.append(_write(p(f"owner_investment_{year}.csv"), ["Item", "Amount"],
                        [["Contributed", _money(oi["contributed"])], ["Paid for company (owed)", _money(oi["lent"])],
                         ["Reimbursed", _money(oi["reimbursed"])], ["Drawn", _money(oi["drawn"])],
                         ["Owed to owner at year end", _money(oi["owed_now"])],
                         ["Net invested (lifetime)", _money(oi["net_investment"])]]))

    inv_rows = []
    for it in db.get_items(active_only=False):
        r = run_costing(db.get_moves(it["id"]), it.get("cost_method") or "fifo", end)
        if r["qty"] or r["value"]:
            inv_rows.append([it["name"], it.get("sku", ""), it.get("cost_method", "fifo"),
                             f"{r['qty']}", _money(r["unit_cost"]), _money(r["value"])])
    files.append(_write(p(f"inventory_valuation_{end}.csv"),
                        ["Item", "SKU", "Method", "Quantity", "Unit cost", "Value"], inv_rows))

    for doc_type, name in (("invoice", "accounts_receivable"), ("bill", "accounts_payable")):
        rep = aging([{"contact": d["contact"], "due_date": d.get("due_date", ""), "balance": d["balance"]}
                     for d in db.open_documents(doc_type, end)], end)
        files.append(_write(p(f"{name}_aging_{end}.csv"), ["Contact"] + AGING_BUCKETS + ["Total"],
                            [[k] + [_money(v) for v in vals] for k, vals in sorted(rep["rows"].items())]
                            + [["Total"] + [_money(v) for v in rep["totals"]]]))

    files.append(_write(
        p(f"transactions_{year}.csv"),
        ["Date", "Account", "Payee", "Memo", "Category", "Schedule C line", "Amount", "Transfer",
         "Contact", "Paid by", "Reimbursable"],
        [[t["date"], acct_names.get(t["account_id"], ""), t.get("payee", ""), t.get("memo", ""),
          cats.get(t.get("category_id", ""), {}).get("name", ""),
          cats.get(t.get("category_id", ""), {}).get("tax_line", ""), t.get("amount", ""),
          "yes" if str(t.get("is_transfer")) == "1" else "", (db.get_contact(t.get("contact_id", "")) or {}).get("name", ""),
          "owner" if t.get("paid_by") == db.owner_person_id() else t.get("paid_by", ""),
          "yes" if str(t.get("reimbursable")) == "1" else ""]
         for t in sorted(db.get_transactions(start=start, end=end), key=lambda x: x["date"])]))

    with open(p("README.txt"), "w", encoding="utf-8") as f:
        f.write(f"FinanceBook year-end package for {year}\nCreated {date.today().isoformat()} from "
                f"\"{db.get_setting('database_name', '')}\".\n\n"
                "Cash-basis books. Depreciation, cost of goods sold and asset gains are computed "
                "by FinanceBook (not transactions). Schedule C figures, payroll, 1099 and tax "
                "amounts are bookkeeping ESTIMATES — please review with your accountant.\n"
                f"Net profit (estimate): {fmt(sc['net_profit'])}\n"
                f"Unmapped to a Schedule C line: {fmt(sc['unmapped'])}\n")
    files.append(p("README.txt"))
    return files


def _line_sort(code: str):
    num = "".join(ch for ch in code if ch.isdigit())
    return (int(num) if num else 999, code)
