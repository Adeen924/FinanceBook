# FinanceBook — Business Books Audit & Plan

*Written 2026-11-02 for v1.0.22 → business features. Owner: Adib Mazloom.*

This is the step-one audit requested before building business bookkeeping into
FinanceBook. It covers what exists, what is missing, US sole-proprietor
requirements (with sources), and the phased plan being shipped.

> **Not tax advice.** Every tax figure below is an estimate for record-keeping.
> Confirm treatment with a CPA/EA before filing.

---

## 1. What already exists (confirmed by reading the code)

Your preliminary finding is **correct**, with these nuances:

| Area | Where | Notes |
|---|---|---|
| Accounts | `accounts` table, `pages/accounts.py` | Types: checking, savings, credit card, gift card, loan, investment, cash, other. Active/inactive. Opening balance. Currency column exists but is not used in math. |
| Transactions | `transactions` table | Single-entry: one account + one category per row. Amount `REAL`, signed (money out negative). |
| Categories | `categories` (parent/child, type `income`/`expense`/`debt_repayment`) | Personal tree seeded once per new DB (`default_categories.py`). |
| Classes | `classes` | Orthogonal dimension; filter in Reports only. **Not editable on the transaction dialog** (column was removed from the register). |
| Splits | `split_group_id` siblings | `split_transaction()` — one payment, N category lines. Works with transfers. |
| Transfers | `is_transfer` + `transfer_pair_id` | `save_transfer_pair()` creates the mirror; excluded from P&L. |
| Loans | `loans` table + `utils/loan.py` | Pure amortization math. Principal/interest stored on payment txns. |
| Rules | `rules` table | Payee/memo/amount/date matchers; applied at import and on demand. |
| Reconciliation | `reconciliations` | Statement balance, cleared balance, live diff. |
| Reports | `pages/reports.py` | P&L (cash basis, by category tree, class filter) and Balance Sheet. CSV export. |
| Import | QFX/QBO/OFX, CSV/XLSX (auto-detect), QuickBooks IIF + Excel wizard | Duplicate detection via `import_hash`. |
| Multi-DB | `db_config.py` | Any number of `.db` files anywhere (OneDrive etc.), switched from the sidebar. |
| Updates | thin frozen launcher + swappable `app/` | Tag push → GitHub Actions → one-click update. |

### Corrections / things the preliminary finding missed

1. **The Balance Sheet does not have an equity section and does not decompose.**
   It shows Assets, Liabilities, and "Net Worth / Equity = Assets + Liabilities".
   That always "balances" trivially, but it cannot show owner capital, draws,
   retained earnings, or why net worth differs from cumulative profit.
2. **Inactive accounts are dropped from the Balance Sheet** even if they still
   have a balance. Their transactions still count in the P&L, so net worth and
   cumulative profit can disagree.
3. **Debt repayment** transactions (category type `debt_repayment`) reduce cash
   but nothing reduces the liability account unless the user also records a
   transfer, so the liability side can be overstated. The new Equity section
   surfaces this as a labelled "Other adjustments" line instead of hiding it.
4. **No schema version.** `_migrate()` blindly retries `ALTER TABLE` on every
   launch and there is no backup before migrating.
5. **Every known database is opened (and migrated) just to read its name** for
   the sidebar dropdown (`_read_db_name`). With backups-before-migration this
   would back up every DB on every listing, so it now reads the name read-only.
6. Amount totals use `float` everywhere (e.g. `sum(float(...))`). New code
   sums through `utils/money.py` (Decimal, cents, half-up).
7. **Packaging constraint discovered during the audit:** the frozen launcher
   only contains modules PyInstaller found from `launcher.py` + `hiddenimports`.
   A scratch PyInstaller build confirmed the bundle already includes
   `decimal`, `hashlib`, `html.parser`, `email.*`, `zipfile`, `csv`, `json`,
   `statistics`, `ssl`/`requests` — everything planned here. **No new
   dependency and no change to the spec/launcher is needed.**

---

## 2. What is missing (all of section 2 of the request)

Owner/equity model, people, receipts + line items + attachments, fixed assets
& depreciation, contacts/invoices/bills/AR/AP, Schedule-C-mapped categories,
inventory & costing, payroll & 1099s, sales tax, estimated taxes, mileage,
home office, Schedule C summary, cash flow statement, budgets, recurring
transactions, audit trail, backups/restore, period lock, grouped sidebar.

---

## 3. What a US sole proprietor / single-member LLC needs

A single-member LLC is a "disregarded entity" by default and files like a sole
proprietor: **Schedule C** with Form 1040, **Schedule SE** for
self-employment tax, and quarterly **1040-ES** estimates.

### Schedule C lines the category map uses

| Line | Name | Seeded categories |
|---|---|---|
| 1 | Gross receipts or sales | Sales, Services, Shipping Income |
| 2 | Returns and allowances | Refunds / Returns |
| 4 | Cost of goods sold (Part III) | Cost of Goods Sold |
| 6 | Other income | Other Income |
| 8 | Advertising | Advertising & Marketing (Online Ads, Social Media, SEO, Sponsorships, Promotional Items, Print & Signage, Events & Trade Shows) |
| 9 | Car and truck | Car & Truck (Fuel, Repairs, Parking & Tolls, Mileage Deduction) |
| 10 | Commissions and fees | Commissions & Fees, Merchant / Processing Fees |
| 11 | Contract labor | Contract Labor |
| 13 | Depreciation / §179 | Depreciation |
| 15 | Insurance (other than health) | Insurance |
| 16b | Interest (other) | Interest |
| 17 | Legal and professional | Legal & Professional |
| 18 | Office expense | Office Expense (incl. Postage) |
| 20a/20b | Rent or lease | Rent / Lease (Equipment, Business Property) |
| 21 | Repairs and maintenance | Repairs & Maintenance |
| 22 | Supplies | Supplies |
| 23 | Taxes and licenses | Taxes & Licenses |
| 24a | Travel | Travel |
| 24b | Deductible meals | Meals (flagged 50%) |
| 25 | Utilities | Utilities (Phone & Internet) |
| 26 | Wages | Wages, Payroll Taxes (employer share → line 23) |
| 27b | Other expenses (Part V) | Software & Subscriptions, Shipping, Bank Fees, Education, Other |
| 30 | Business use of home | Home Office |

### Things the request did not mention (added to the plan)

- **The 1099-NEC threshold is no longer $600.** For payments made **after
  Dec 31, 2025** it is **$2,000** (OBBBA), inflation-indexed from 2027.
  Royalties stay $10, attorney gross proceeds stay $600. Deadline for 2026
  payments: **Feb 1, 2027** (Jan 31 is a Sunday). The app stores the threshold
  in the dated data file, not in code.
  Source: <https://www.irs.gov/instructions/i1099mec>
- **Mileage rate changed mid-2026:** 72.5¢ (Jan–Jun) and 76¢ (Jul–Dec). The
  mileage log therefore needs date-ranged rates.
  Source: <https://www.irs.gov/tax-professionals/standard-mileage-rates>
- **Employer-provided meals are 0% deductible from 2026** (§274(o)); ordinary
  business meals remain 50%. Source: Pub 15-B (2026).
- **Self-employment tax** (15.3% on 92.35% of net profit; SS portion capped at
  the $184,500 wage base) drives the quarterly estimate, not just income tax.
- **QBI deduction (§199A)** — 20%, now permanent; worth showing on the estimate
  as an informational line.
- **Section 179** 2026 limit $2,560,000 (phase-out from $4,090,000); **bonus
  depreciation is 100%** for property acquired after Jan 19, 2025.
  Source: Rev. Proc. 2025-32, IRS bonus-depreciation guidance (Notice 2026-11).
- **Cash vs. accrual basis.** Almost every sole proprietor without inventory
  over the gross-receipts test uses **cash basis**. FinanceBook's P&L is cash
  basis; invoices/bills give AR/AP *tracking* and aging reports, and recognise
  revenue when paid. An accrual toggle is listed as future work.
- **Separate business account.** Not legally required for a sole prop, but
  strongly recommended for an LLC (helps preserve liability protection). The
  "I opened a business bank account" action makes the switch clean.
- **Record retention** — keep receipts ≥3 years (7 for some items); receipts
  are copied next to the database so they sync and back up with it.
- **State items** (sales tax registration, state income tax withholding, SUTA
  rates) vary by state — stored as user-entered rates, never assumed.
- **FUTA credit reduction** — California's 2026 reduction is not final until
  after Nov 10, 2026 (potential 1.5%–5.3%). Marked TODO in the data file.

### Verified 2026 figures (stored in `app/data/tax_tables_2026.json`)

| Item | 2026 value | Source |
|---|---|---|
| Social Security wage base | $184,500 | Pub 15 (2026) |
| SS / Medicare rates | 6.2% / 1.45% each side; Addl. Medicare 0.9% over $200k (employee) | Pub 15 |
| FUTA | 6.0% on first $7,000, 0.6% net after credit | Pub 15 §14 |
| Standard deduction | $16,100 single, $32,200 MFJ, $24,150 HOH | Rev. Proc. 2025-32 |
| Federal withholding | Pub 15-T (2026) Worksheet 1A, standard schedules | Pub 15-T p.12 |
| 1040-ES due dates (TY2026) | Apr 15, Jun 15, Sep 15 2026; Jan 15 2027 | 2026 Form 1040-ES |
| Home office (simplified) | $5/sq ft, max 300 sq ft | IRS |
| MACRS half-year GDS table | Pub 946 Table A-1 (3/5/7/10/15/20-yr) | Pub 946 App. A |

**Could not verify (marked TODO, not guessed):** final 2026 FUTA
credit-reduction states; 2027 SS wage base ($192,300 seen only second-hand);
state withholding/SUTA tables (user enters their own rate).

---

## 4. Design decisions

### Owner-paid expenses without a business bank account (foundation)

FinanceBook stays single-entry; the owner model uses **built-in non-bank
accounts** so every existing report keeps working:

| Account (role) | Type | Meaning of balance |
|---|---|---|
| Owner's Equity / Capital Contributions (`owner_capital`) | equity | negative = capital you've put in |
| Owner's Draws (`owner_draws`) | equity | positive = money you've taken out |
| Due to Owner (`due_to_owner`) | liability | negative = what the company owes you |
| Petty Cash (`petty_cash`) | cash (asset) | cash on hand |
| Owner-paid (personal card/cash) (`owner_paid`) | equity *or* liability, your choice | extra owner funding source |

- **Capital contribution** = record the expense *in* the Capital account.
- **Reimbursable / loan from owner** = record it *in* Due to Owner
  (`reimbursable=1`). Shows as a liability.
- **Company account** = record it in a real bank/card account (normal).
- **Reimburse owner** = transfer from a real account → Due to Owner.
- **Owner draw** = transfer from a real account → Owner's Draws.
- **Convert what I'm owed into capital** = transfer Capital → Due to Owner.
- **I opened a business bank account** = create the account, optionally record
  the opening deposit as a contribution, and make it the default funding source
  so new entries default to "Company account". History is left as recorded.

The transaction dialog (business books) gets **Paid by** (Company / Owner /
named person) with an Owner treatment choice, **Charged to** (Company, a
person, a class/project/campaign; customers later) and keeps `reimbursable`.

### Balance Sheet equity

`utils/ledger.balance_sheet()` (pure, tested) computes:
Opening balance equity + Owner contributions − Owner draws + Retained earnings
(prior years) + Net income (this year) [+ Other adjustments]. "Other
adjustments" is only non-zero when money moved through non-P&L categories
without an offsetting balance-sheet entry (e.g. loan principal paid without a
liability transfer) or a transfer lost its other half; the tests prove it equals
exactly that amount. Computed assets/liabilities from later phases (fixed
assets at NBV, inventory, payroll and sales-tax liabilities) plug into the same
function with their matching non-cash P&L amounts.

### Data safety

- `PRAGMA user_version` schema versioning; before *any* migration of an existing
  file the DB is copied (SQLite online-backup API, WAL-safe) to
  `FinanceBook Backups/<name>-pre-migration-vN-<timestamp>.db` next to it.
- Columns are only ever **added**; existing rows are never rewritten.
- `save_transaction()` only writes the new columns when the caller supplies
  them, so older code paths can't blank them.
- Automatic daily backup on open (keep last N, default 10), one-click
  backup/restore in Settings (restore first backs up the current file).

### Personal vs Business

`settings.book_type` = `personal` (default for every existing DB) or
`business`. Business books seed Schedule-C categories, built-in owner accounts
and an "Owner" person, and show the Business sidebar section. Switching an
existing DB to Business is additive (adds categories/accounts, removes nothing).

---

## 5. Phased plan (each phase ships as its own minor release)

| Phase | Version | Contents |
|---|---|---|
| 1 | v1.1.0 | Book type; owner/equity model (A); Schedule C categories + `tax_line`/meals % (E); backups + restore (J); Owner Investment page/report; grouped sidebar; BS equity section |
| 2 | v1.2.0 | Receipts (B): files copied to `receipts/`, sha256 dedupe, stdlib text/eml/html/PDF parsing, opt-in Anthropic vision, review screen, line items → split txn, `receipt_items`, vendor memory, paperclip attachments on any txn |
| 3 | v1.3.0 | Fixed assets & depreciation (C): SL, DB, units, §179/bonus, MACRS GDS half-year (Pub 946 Table A-1), prepaid amortization, dispose with gain/loss; P&L depreciation + BS NBV |
| 4 | v1.4.0 | Contacts, invoices (QTextDocument → PDF), bills, AR/AP aging, dashboard widget (D) |
| 5 | v1.5.0 | Inventory (F): items, movements, FIFO & weighted average, COGS, valuation |
| 6 | v1.6.0 | People/payroll (G): W-2 vs 1099, time, payroll runs from dated tax tables, liabilities, 1099 summary at the data-file threshold |
| 7 | v1.7.0 | Taxes & reports (H, I, J): sales tax, estimated taxes, mileage, home office, Schedule C summary, year-end export, cash flow, P&L by class/month, vendor/person, budgets, recurring, audit trail, period lock, business dashboard |

**Explicit TODO / future work:** accrual-basis toggle; multi-currency (the
`currency` column is kept and new money columns carry no implicit currency, so
it can be added later); MACRS mid-quarter convention and ADS tables (only GDS
half-year is implemented); state payroll tax tables; e-filing.
