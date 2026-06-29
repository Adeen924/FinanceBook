# FinanceBook — Project Handoff

Use this document to orient a new Claude Code session to the project.
Paste it at the start of a new conversation or reference it with `@HANDOFF.md`.

---

## What this is

A **native desktop personal finance application** built with PyQt6 + SQLite.
Think QuickBooks Lite — multiple bank accounts, transaction import, categories,
reconciliation, P&L / Balance Sheet reports, auto-categorization rules, and
multiple separate databases (personal vs. company vs. property).

Runs on Windows. Data lives in a local `.db` file that OneDrive/Google Drive
syncs automatically. No web server, no cloud account, no internet required
(except for the optional update check).

---

## How to run (development)

```
double-click run.bat
```

`run.bat` creates `.venv`, installs deps with `--prefer-binary`, runs `main.py`.

## How to build a distributable zip

```
double-click build.bat
```

Requires PyInstaller (installed automatically by the script).
Output: `dist\FinanceBook.zip` — portable, no installation needed.
Users extract the zip anywhere and double-click `FinanceBook.exe`.

---

## Project layout

```
main.py                  Entry point. Opens finances.db, shows first-run naming
                         dialog, launches MainWindow, fires update check.
updater.py               Background OTA update checker. Fetches version.json
                         from adibmazloom.com, shows dialog if newer version exists.
run.bat                  Dev launcher (creates venv, installs deps, runs app)
build.bat                Build script → dist\FinanceBook.zip
FinanceBook.spec         PyInstaller config (all hidden imports listed explicitly)
requirements.txt         Runtime deps: PyQt6, ofxparse, pandas, openpyxl,
                         python-dateutil, requests, packaging

sheets/
  client.py              SQLite backend — ALL database logic lives here.
                         Class: Database. Tables: accounts, transactions,
                         categories, classes, reconciliations, rules, settings, loans.

parsers/
  qfx.py                 Parses .qfx / .qbo (OFX) bank export files
  spreadsheet.py         Parses .csv / .xlsx with auto column detection;
                         also parse_qb_multi_account_excel() for QB Transaction
                         Detail by Account reports
  iif.py                 Parses QuickBooks IIF exports — returns 4-tuple
                         (accounts, categories, transactions, warnings)

utils/
  loan.py                Pure amortization math — no DB/UI imports.
                         calc_monthly_payment(), amortization_schedule(),
                         split_for_date(), remaining_balance()

ui/
  styles.py              Global QSS stylesheet + color constants
  widgets.py             Shared widgets: NavButton, DataTable, PageTitle, etc.
  main_window.py         MainWindow — sidebar nav, database selector dropdown,
                         lazy page loading, database switching
  pages/
    dashboard.py         Net worth, account cards, recent transactions
    accounts.py          Account list — shows current balance only (no opening balance col)
    loans.py             Loan list + amortization schedule viewer
    transactions.py      Full transaction list with filters, quick-categorize
    transfers.py         Auto-detect & link transfers between own accounts
    import_page.py       File import (QFX/QBO/CSV/XLSX) with rule auto-apply
    rules.py             Auto-categorization rules CRUD
    categories.py        Category + class tree management
    reconcile.py         Bank statement reconciliation with live diff counter
    reports.py           P&L and Balance Sheet with CSV export
    settings.py          QuickBooks migration (IIF + Excel), DB rename,
                         Reset Database (requires typing "DELETE")
  dialogs/
    account_dialog.py    Add/edit account (with institution autocomplete)
    loan_dialog.py       Add/edit loan (principal, rate, term, first payment date,
                         interest category, linked account)
    transaction_dialog.py Add/edit transaction — includes Loan Payment section
    rule_dialog.py       Add/edit auto-categorization rule
```

---

## Database (sheets/client.py)

Single SQLite file. WAL journal mode. Tables:

| Table | Purpose |
|---|---|
| `accounts` | Bank accounts (checking, savings, credit card, loan, etc.) |
| `transactions` | All transactions. `is_transfer=1` → excluded from P&L. Loan payments carry `loan_id`, `principal_amount`, `interest_amount` |
| `categories` | Income/expense categories with parent_id for sub-categories |
| `classes` | Orthogonal dimension (departments/projects) |
| `reconciliations` | Reconciliation sessions per account |
| `rules` | Auto-categorization rules (field + operator + value → category) |
| `loans` | Amortizing loan definitions. Fields: `original_principal`, `annual_rate` (decimal, e.g. 0.065), `term_months`, `start_date` (first payment date), `payment_amount` (0 = auto-calc), `account_id` (linked liability account), `interest_category_id` |
| `settings` | Key/value store — `database_name` is the main entry |

Boolean fields (`is_transfer`, `reconciled`, `active`) are stored as INTEGER
but returned as string "0"/"1" for UI compatibility.

Amount fields stored as REAL, returned as string.

**Key methods:**
- `apply_rules(txns)` — applies active rules to a list of txn dicts in-place
- `apply_rules_to_all()` — re-categorizes all uncategorized transactions, returns count
- `get_setting(key)` / `set_setting(key, value)` — app settings
- `get_institutions()` — distinct institution names for autocomplete
- `get_loans()` / `save_loan(data)` / `delete_loan(id)` — loan CRUD
- `get_loan_transactions(loan_id)` — transactions linked to a specific loan
- `clear_all_data()` — wipes all tables including loans (reset feature in Settings)

**DB migration**: `_migrate()` runs at startup and idempotently adds new columns with
`ALTER TABLE … ADD COLUMN` wrapped in try/except. Currently adds:
`transactions.loan_id`, `transactions.principal_amount`, `transactions.interest_amount`.

---

## Multiple databases

All `.db` files in the same directory as `finances.db` appear in the sidebar
dropdown. "➕ New Database…" creates a new file. Switching databases invalidates
all lazy-loaded page caches and re-navigates to Dashboard.

Database name is stored in the `settings` table under key `database_name`.
First launch shows a QInputDialog to name the initial database (default: "Personal Finances").

In a packaged build (`sys.frozen = True`), the DB lives at
`%APPDATA%\FinanceBook\finances.db` (writable, survives reinstalls).
In development (running from source), it lives next to `main.py`
(OneDrive-synced for the owner's own use).

---

## Auto-categorization rules

Rules run in priority order (higher number first). First match wins.
Uncategorized transactions only — already-categorized ones are never overwritten.

Fields: `payee`, `memo`, `amount`, `date`
Operators: `contains`, `equals`, `starts_with`, `ends_with`, `gt`, `lt`, `between`, `day_of_month`

Rules are applied:
1. Automatically during import (Import tab)
2. On demand via "Apply to All Transactions" button (Rules tab)

---

## Transfers

Transfers between own accounts are detected automatically (same absolute amount,
opposite sign, ≤3 days apart, different accounts). Linked transfers set
`is_transfer=1` on both sides and are excluded from P&L / Balance Sheet.

---

## QuickBooks migration (Settings tab)

`.QBB` files cannot be read directly — they are proprietary encrypted backups.

**Workflow:**
1. **Accounts**: QuickBooks → File → Utilities → Export → Lists to IIF Files
   → load in Settings tab → creates accounts with opening balances
2. **Transactions**: QuickBooks → Reports → Accountant & Taxes →
   Transaction Detail by Account → export to Excel → import via Import tab

The IIF parser (`parsers/iif.py`) handles quoted numbers (`"1,332.73"`),
multiple account types, and reads `OBAMOUNT` for opening balances.

---

## OTA update check (updater.py)

- `CURRENT_VERSION = "1.0.0"` — bump this in each new build
- `VERSION_URL` → `https://adibmazloom.com/financebook/version.json`
- Runs on a daemon thread at startup, never blocks GUI
- Shows dialog only if remote `latest_version > CURRENT_VERSION`
- Download button opens `download_url` in browser (GitHub release zip)

**Current manual release steps (to be automated — see below):**
1. Bump `CURRENT_VERSION` in `updater.py`
2. Run `build.bat` → get `dist\FinanceBook.zip`
3. Upload zip to GitHub releases
4. Update `version.json` on `adibmazloom.com`

**Planned: release automation script**
Goal: one command does steps 1–4 automatically. Three options under consideration:

- **Option A — `release.py`**: single Python script in the project folder.
  `python release.py 1.0.2 "Fixed bug X"` bumps the version, runs `build.bat`,
  creates the GitHub release via `gh` CLI, uploads the zip, updates `version.json`.
  Best if releasing from the same Windows machine every time.

- **Option B — GitHub Actions**: a workflow file in `.github/workflows/release.yml`.
  Trigger manually on GitHub.com → Actions → "Run workflow" → enter version + notes.
  GitHub builds the app in the cloud and handles everything. Works from any browser.
  Requires Windows runner and all deps installable in CI (may need to pin versions).

- **Option C — `release.ps1`**: PowerShell script, no Python dependency beyond
  the venv. Double-click or run from any terminal on Windows. Slightly less flexible
  than Option A but zero extra tooling.

**Decision needed before building:**
1. Which option (A, B, or C)?
2. Should the script auto-bump `CURRENT_VERSION` in `updater.py`, or do you set it manually first?

---

## Loan tracking

Loans live on the **Loans** page (sidebar, between Accounts and Transactions).

**Creating a loan** (loan_dialog.py):
- Inputs: name, lender, original principal, annual rate (%), term in months,
  first payment date, monthly payment override (0 = auto-calculate), linked
  liability account, interest expense category.
- Monthly payment uses the standard PMT formula in `utils/loan.py`.

**Recording a loan payment** (transaction_dialog.py):
- Check "This is a loan payment", pick the loan from the dropdown.
- The dialog shows a live split preview: "Payment #6 — Principal $622.81, Interest $713.00"
  using `split_for_date(loan, txn_date, abs(amount))`.
- If the payment is larger than the scheduled amount, the extra all goes to principal.
- `get_data()` writes `loan_id`, `principal_amount`, `interest_amount` onto the transaction.
- The interest category auto-fills from the loan's `interest_category_id`.

**Remaining balance** is computed as:
  `original_principal − SUM(principal_amount)` across all linked transactions.
  There is no separate balance column — it is always derived live.

**Amortization schedule dialog** (loans.py `_show_schedule`):
- Opens from the "Schedule" button on the Loans page.
- Rows with a due_date matching a paid transaction's date are highlighted green.

**`utils/loan.py`** — pure math, no DB/UI imports:
- `calc_monthly_payment(principal, annual_rate, term_months)` → float
- `amortization_schedule(loan_dict)` → list of {payment_number, due_date, payment, principal, interest, balance}
- `split_for_date(loan_dict, txn_date, actual_amount)` → {payment_number, scheduled_payment, interest, principal, balance_before, balance_after}
- `remaining_balance(loan_dict, paid_transactions)` → float

**`annual_rate` storage**: stored as a decimal in the DB (e.g., `0.065` for 6.5%).
The loan dialog displays and accepts it as a percentage (`6.5 %`) and divides by 100 on save.

---

## Known issues / things to watch

- **Button clipping in tables**: All inline action buttons (Edit/Delete/Reconcile)
  use `setStyleSheet("padding: 4px 12px; ...")` and explicit `setRowHeight(row, 44)`
  to avoid the global QSS `padding: 7px 16px` clipping them. If adding new
  table action buttons, follow this pattern.

- **PyInstaller + OneDrive**: `build.bat` runs PyInstaller in `C:\Temp\FinanceBook_build\`
  (outside OneDrive) to avoid file-lock errors during cleanup.

- **Dynamic imports**: `main_window.py` uses `_page_constructors()` with explicit
  `from ui.pages.X import Y` statements (not `importlib`) so PyInstaller can
  detect all modules statically. All pages/dialogs are also listed in
  `hiddenimports` in `FinanceBook.spec` as a belt-and-suspenders measure.

- **Python 3.14**: Project uses Python 3.14.3. `dict | None` return type hints
  require 3.10+. No known issues beyond what's in requirements.txt.

---

## Style conventions

- PyQt6 throughout (not PyQt5 / PySide)
- QSS in `ui/styles.py` — colors as module-level constants
- Action buttons in table cells must override padding via `setStyleSheet()`
- No comments unless the WHY is non-obvious
- `db.get_*` methods return list/dict with booleans as "0"/"1" strings
- New pages: add to `NAV_ITEMS`, `_page_constructors()`, and `hiddenimports` in `FinanceBook.spec`.
  **NOTE: `LoansPage` and `LoanDialog` were added in the loans feature but `FinanceBook.spec`
  `hiddenimports` has not been updated yet — do this before the next build.**

---

## Owner

Adib Mazloom — `amazloom@fiyrpod.com`
Website: `adibmazloom.com`
GitHub: `github.com/Adeen924`
