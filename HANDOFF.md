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
  chart_of_accounts.py   Qt-free: account groups/order, "New Account" types, the
                         unified Account picker entries ("acct:<id>" = account)
  rules.py               Qt-free: rule condition fields/operators, matching, text
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
    accounts.py          "Accounts" = Chart of Accounts: accounts + categories in one
                         list grouped by type (utils/chart_of_accounts.py), tabs
                         Chart of Accounts / Inactive Accounts / Classes
    loans.py             Loan list + amortization schedule viewer
    transactions.py      Full transaction list with filters, quick-categorize
    transfers.py         Auto-detect & link transfers between own accounts
    import_page.py       File import (QFX/QBO/CSV/XLSX) with rule auto-apply
    rules.py             Bank rules list (conditions text, double-click to edit)
    categories.py        CategoryDialog, ClassDialog, ClassesPanel (no page of its own
                         since v1.14 — the old "Categories" id is an alias of Accounts)
    reconcile.py         Bank statement reconciliation with live diff counter
    reports.py           P&L and Balance Sheet with CSV export
    settings.py          QuickBooks migration (IIF + Excel), DB rename,
                         Reset Database (requires typing "DELETE")
  dialogs/
    account_dialog.py    Add/edit account (with institution autocomplete)
    loan_dialog.py       Add/edit loan (principal, rate, term, first payment date,
                         interest category, linked account)
    transaction_dialog.py Add/edit transaction — includes Loan Payment section
    rule_dialog.py       Add/edit bank rule: All/Any of condition rows + Set Account
```

---

## Database (sheets/client.py)

Single SQLite file. Rollback-journal (DELETE) mode since v1.9 — was WAL;
see "Connect existing database + lock file" below. Tables:

| Table | Purpose |
|---|---|
| `accounts` | Bank accounts (checking, savings, credit card, loan, etc.). `parent_id` (v1.15, schema 13) = top-level account in the same Chart of Accounts group, or '' |
| `transactions` | All transactions. `is_transfer=1` → excluded from P&L. Loan payments carry `loan_id`, `principal_amount`, `interest_amount` |
| `categories` | Income/expense categories with parent_id for sub-categories |
| `classes` | Orthogonal dimension (departments/projects) |
| `reconciliations` | Reconciliation sessions per account |
| `rules` | Bank rules: match_mode, action (`category_id` or `transfer_account_id`); first condition mirrored in field/operator/value/value2 |
| `rule_conditions` | A rule's conditions (v1.14, schema 12) — see "Bank rules" |
| `loans` | Amortizing loan definitions. Fields: `original_principal`, `annual_rate` (decimal, e.g. 0.065), `term_months`, `start_date` (first payment date), `payment_amount` (0 = auto-calc), `account_id` (linked liability account), `interest_category_id` |
| `settings` | Key/value store — `database_name` is the main entry |

Boolean fields (`is_transfer`, `reconciled`, `active`) are stored as INTEGER
but returned as string "0"/"1" for UI compatibility.

Amount fields stored as REAL, returned as string.

**Key methods:**
- `apply_rules(txns)` — applies active rules to a list of txn dicts in-place
- `apply_rules_to_all()` — re-categorizes all uncategorized transactions (empty or
  deleted category; skips the locked period, catches errors per transaction);
  returns an `ApplyResult` (compares as the int count; `.categorized`,
  `.skipped_locked`, `.failed`, `.message()`)
- `uncategorized_txns()` / `uncategorized_count()` — via `ledger.is_uncategorized`
- `find_flipped_imports()` / `flip_transaction_signs(ids)` — the v1.15 sign check
- `move_category(id, parent)` / `move_account(id, parent)` / `rolled_up_balance(id)`
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

## Connect existing database + lock file (v1.9)

For one database used from two computers via OneDrive. Files:

- `db_connect.py` — read-only checks before connecting a file
  (`validate_existing_database`: exists, not 0-byte, not a cloud placeholder,
  SQLite header, FinanceBook tables, `user_version` not newer; warns on OneDrive
  conflict copies, leftover `-wal`, `-journal`). Never opens via `Database(...)`.
- `db_lock.py` — `<db>.lock` JSON sidecar (machine, user, pid, app_version,
  acquired_at, last_heartbeat, session_id). `O_EXCL` create; stale if heartbeat
  older than `STALE_AFTER_SECONDS` (12 min) or same machine + dead pid; corrupt =
  stale. `HEARTBEAT_SECONDS` = 90. Released on switch, window close, atexit,
  and before the updater's `os._exit`.
- `ui/connect_dialog.py` — file picker + confirmations, `LockConflictDialog`
  (Read-Only / Take Over / Cancel), and `open_with_lock()`, which every path
  that opens a database as the active one goes through (main.py startup,
  `MainWindow.open_database`).
- `Database(path, read_only=True)` — no schema init/migration/triggers;
  `_conn()` sets `PRAGMA query_only=ON`; auto-backup, `set_session_user`'s write
  and `process_recurring` are no-ops. `Database.__init__` raises
  `NewerSchemaError` for `user_version > SCHEMA_VERSION` on every path.
- Journal mode is DELETE so the `.db` alone is complete after each commit;
  `_conn()` converts old WAL files on first use, `Database.close()` checkpoints.
- MainWindow: heartbeat `QTimer`; losing the lock (taken over) or waking after a
  gap longer than the stale threshold flips the shared `db.read_only` flag and
  shows the yellow banner.
- Test-only env overrides: `FINANCEBOOK_SETTINGS_DIR`, `FINANCEBOOK_MACHINE_NAME`.
- Tests: `tests/test_db_lock.py`, `tests/test_connect_database.py`,
  `tests/test_connect_ui.py` (offscreen Qt).

The lock is best-effort across computers (sync delay); see the README.

---

## Receipt reading, shipping, inventory lines (v1.10, schema 10)

- `parsers/pdf_text.py` — stdlib PDF reader: object scan (+ object streams /
  xref streams), page tree, Flate/LZW/AHx/A85 + PNG predictors, content-stream
  interpreter (CTM, Tm/Td/TD/T*/'/", Tj/TJ), simple fonts (WinAnsi/MacRoman/
  Standard + Differences, Widths) and Type0 fonts (Identity-H/embedded CMap, /W)
  with ToUnicode. `glyphs_to_text()` groups glyphs by baseline, orders by x and
  turns wide gaps into 3+ spaces (cells). Only pages' content is read (orphan
  streams are ignored); rotated text is dropped; `MAX_PAGES` = 30. Encrypted →
  `PDFEncrypted`. `receipt.pdf_to_text` falls back to the old regex reader for
  page-less files.
- `parsers/ocr.py` — Windows.Media.Ocr via `powershell -EncodedCommand`
  (no .ps1 file, no execution-policy issue, `CREATE_NO_WINDOW`), image path in
  env var `FB_OCR_IMAGE`; returns word boxes rebuilt with `glyphs_to_text`.
  Used for images and image-only PDFs (embedded DCT JPEGs). `parse_file` can
  take seconds, so `ReceiptsPage` runs it in `ParseWorker` (QThread) behind a
  progress dialog.
- `parsers/receipt.py` — result dict gained `shipping`, `discount` (positive),
  `duties`, `order_number`, `date_note`, item `sku`. `GENERIC_LABELS` +
  `Summary` + `finish()` = the shared "label next to an amount" logic; subtotal
  is items only. `parse_date_info()` reports ambiguous numeric dates.
  `register_vendor_parser()`; vendors live in `parsers/receipt_vendors.py`
  (Amazon, DigiKey, JLCPCB). AI schema has the new fields (old replies still
  normalise).
- `utils/receipt_booking.py` — `item_extras()/landed_costs()`: discount always
  spread over items; separate mode books the expense lines' share of tax/tip/
  shipping/duties as own lines but keeps the inventory/asset lines' share on
  them (landed cost, not double-expensed).
- Schema 10: `receipts.shipping/discount/duties`, `receipt_items.item_id/sku`.
  `receive_receipt_inventory()` deletes the receipt's previous purchase moves
  (by `receipt_id`) before adding, so re-booking never doubles stock;
  `book_receipt()` reuses the receipt's own transaction if called again.
- Receipt copies go to `FinanceBook Receipts/<db file stem>/` next to the database
  (`receipts_dir()`), never the user's own `receipts` folder; copies made by
  v1.10.0 and older stay valid (relative paths) and Settings → Receipt Files →
  Move Older Copies moves only recorded files (copy, SHA-256 check, re-point, delete).
- Schema 11 (v1.11): `receipt_items.units_per_pack`, `items.pack_size` (whole ≥ 1,
  default 1; `inventory_store.pack_size_value()`). Review dialog columns COL_UPP /
  COL_UNITS / COL_UCOST; `receipt.suggest_pack_size()` adds `pack_suggestion` to
  parsed items (ambiguous ones are shown, not applied). Stock units = qty × upp at
  `per_unit_cost()` (6 dp). `receive_receipt_inventory()` updates the receipt's
  existing purchase moves in place (same ids) and deletes leftovers. "Save Changes"
  on a booked receipt re-syncs stock without touching money. book_receipt passes
  split outflows as negative (split_transaction only flips expense categories).
- Watched receipts folder (v1.12, no schema change): setting per computer and per
  database in app_settings.json (`db_config.get/set/clear_receipt_folder`, keyed by
  the normcase'd absolute DB path). `utils/receipt_inbox.py` (Qt-free): `scan_folder`
  stat-only listing, matches by original_name + size of FinanceBook's copy, hashes
  only same-size candidates (cache by path/size/mtime), never opens online-only
  placeholders (st_file_attributes recall/offline bits), skips FinanceBook's own
  folders; `receipt_state()`; `import_and_parse()` (offline parsers only, per-file
  failures, cancel between files); `read_with_ai()` only after two confirmations in
  the page. ReceiptsPage: ScanWorker/BatchWorker in module-level `_RUNNING` (a page
  can be destroyed mid-scan); slots must never raise (PyQt6 aborts the app).
- Categories: `Database.get_categories_for_book()` is what pickers use; it hides
  personal-default categories (matched to `default_categories.py`, no tax line)
  in business books unless used, kept, or setting `show_personal_categories`.
  Lookups/reports keep using `get_categories()`.
- Tests: `test_pdf_text.py`, `test_receipt_vendors.py` (an optional class runs
  `_private_samples/` when present — never commit that folder),
  `test_receipt_booking.py`, `test_categories_book.py`, `test_receipt_ui.py`.
  Sanitized fixtures in `tests/fixtures/` (regenerate PDFs with
  `tests/fixtures/make_pdf_fixtures.py`).

---

## Bank rules (v1.14, schema 12)

Rules run in priority order (higher number first). First match wins.
Transactions without an account only — already-categorized ones (and transfers)
are never overwritten.

- A rule has 1+ conditions in `rule_conditions` (id, rule_id, field, operator,
  value, value2, sort) and `rules.match_mode` 'all'|'any'. The rules row's own
  field/operator/value/value2 columns are kept and mirror the FIRST condition
  (old readers, and `save_rule()` callers that still pass a single condition —
  e.g. the QuickBooks import — keep working).
- Migration: `_migrate()` copies each rule's single condition into one
  `rule_conditions` row for rules that have none (idempotent).
- Fields → operators (`utils/rules.py`): `payee` / `memo` (contains, equals,
  starts_with, ends_with), `amount` (equals, gt, lt, between — on the absolute
  amount), `date` (equals, day_of_month), `direction` (is in|out),
  `account` (is <account id>). `describe_rule()` makes the table text.
- Dates (v1.15): `normalize_date()` accepts what DateField accepts (M/D/YYYY,
  M-D-YYYY, YYYY-MM-DD, M/D/YY, M/D); `condition_matches` normalizes both sides
  (old rules may hold "3/15/2026"); the dialog saves YYYY-MM-DD and
  `condition_problem()` validates dates and day_of_month 1–31.
- `direction` reads the stored sign (negative = money out). Every importer
  writes that convention: OFX/QFX/QBO keep the bank's TRNAMT sign, CSV/XLSX
  Debit/Credit use `debit_credit_amount()` (magnitudes), a single Amount column
  is as written unless "reverse the signs" is ticked; IIF imports no
  transactions (chart of accounts only).
- The action is mandatory: `category_id`, or `rules.transfer_account_id` for a
  balance-sheet account. `_apply_category_rules` then sets `txn["_transfer_to"]`
  (skipped when the txn is already in that account); `bulk_save_transactions()`
  and `apply_rules_to_all()` turn it into a transfer with
  `save_transfer_pair(keep_sign=True, match_existing=True)`.

Rules are applied:
1. Automatically during import (Import tab)
2. On demand via "Apply to All Transactions" button (Rules page)

---

## Transfers

Transfers between own accounts are detected automatically (same absolute amount,
opposite sign, ≤3 days apart, different accounts). Linked transfers set
`is_transfer=1` on both sides and are excluded from P&L / Balance Sheet.

Since v1.14 a transfer is usually made by picking a balance-sheet account in the
unified **Account** picker (transaction dialog, split line, register's Set
Account menu, or a rule):
- `save_transfer_pair(src, dest, keep_sign=True)` keeps src's sign (positive =
  money into src from dest); the default (keep_sign=False) still forces "out of
  src" for the older callers (payroll, sales tax, owner dialogs).
- `match_existing=True` links an unlinked entry already in dest (amount exactly
  opposite, ≤ `TRANSFER_MATCH_DAYS` = 3 days, not a split line) instead of
  creating a mirror (`find_transfer_match`).
- Re-pointing to another account or re-filing under a category calls
  `detach_transfer_partner`: a bank entry (import_hash or reconciled) is unlinked
  and left uncategorized; a mirror FinanceBook made is deleted.
- `bulk_save_transactions` lets an imported row adopt a FinanceBook-made mirror
  (same account and amount, ≤3 days, no import_hash) instead of duplicating it.
- Split lines may carry `transfer_to`; each destination gets its own mirror and
  transfer_pair_id. `get_transfer_partner` prefers a row in another account (a
  split's lines to the same destination share a pair id).
- The register's Account column shows "⇄ <other account>" for transfers
  (`transfer_partner_accounts`, one query).

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

## Business sidebar: hubs & optional modules (v1.13, no schema change)

- `utils/nav_config.py` (Qt-free) decides what the sidebar shows:
  `visible_sections(book_type, enabled)`, `locate(name, …)`, the `MODULES`
  (receipts, invoices, owner, taxes, assets, loans, inventory, payroll), the
  `PRESETS` (Simple ⊂ Standard ⊂ Full) and `receipt_line_kinds()`.
- Preference: `settings` key `nav_modules`, JSON
  `{"version":1,"preset":…,"enabled":[…],"known":[…]}`. Missing/empty/corrupt →
  every module on. Names this version doesn't know are ignored but preserved on
  save; modules missing from `known` (added in a later release) default on.
  Personal books ignore the key and show exactly the pre-1.13 list.
- Page names are still the stable IDs (`_nav_to("Import")`, `mw.page("Reconcile")`,
  tour `nav=`, dashboard links). In a Business book some pages live inside a
  `HubPage` (main_window.py): a row of `HubTab` buttons over a stack of the real
  page widgets. `mw.page(name)` returns the page itself; `mw.current_page_widget()`
  is the page on screen (use it instead of `_stack.currentWidget()`, which may be
  a hub). Hub ids: Accounts (+Loans), Transactions (+Import, Reconcile, Transfers,
  Recurring), Invoices & Bills (+Contacts), Categories & Rules.
- Contacts is never hidden (transaction Vendor/customer field, inventory vendors,
  1099 flags, By Vendor report): a tab of Invoices & Bills, or its own item when
  the Invoices module is off.
- `_nav_to(hidden page)` asks "Turn On X?" (`offer_module`); `mw.set_modules()`
  saves and calls `refresh_navigation()`, which keeps the current page (moving it
  into or out of a hub) and deletes every other cached page and hub.
- Hiding a module is navigation-only: reports, COGS, depreciation, payroll
  liabilities, A/R and A/P still include its data. Receipt lines already saved as
  Inventory / Fixed asset keep that type when the module is off.
- New pages: add to `_page_constructors()` and to `nav_config` (`_PERSONAL`,
  `visible_sections`, `ALL_PAGES`, and `MODULES` if optional).

## Chart of Accounts, unified picker, new types (v1.14)

- **Tables stay separate.** `accounts` = balance-sheet accounts; `categories` =
  P&L and capitalising types. Only the UI is unified. `utils/chart_of_accounts.py`
  holds the group order: Bank (checking/savings/cash), Credit Card (credit card,
  credit), Gift Card, Investment, Fixed Assets, Other Assets (other assets,
  other, unknown types), Loan / Liability (loan, liability), Equity | Income,
  Other Income, Expense, Other Expense, Debt Repayment, Asset Purchase,
  Inventory Purchase. `NEW_ACCOUNT_CHOICES` + `resolve_choice()` decide which
  table "+ New Account" saves to (AccountDialog(account_type=…) or
  CategoryDialog(category_type=…)).
- **Page ids.** "Categories" was removed from `_page_constructors()`;
  `nav_config.PAGE_ALIASES` maps "Categories" → "Accounts" and the old business
  hub "Categories & Rules" → "Rules" (now a plain item). `locate()` resolves
  aliases, so `_nav_to`/`mw.page()`/saved links keep working.
- **Picker.** `ui.widgets.AccountPicker` (a FilterComboBox with disabled group
  header rows) lists `get_categories_for_book()` — every category in a personal
  book; a business book still hides UNUSED personal defaults unless "Show
  personal categories" is on (kept deliberately; that checkbox is on the
  Accounts page now) — then the balance-sheet accounts minus the entry's own.
  Values: category id or `acct:<id>`; `.selection()` → (kind, id).
- **Transaction dialog.** The "This is a transfer" checkbox and "Goes to
  account" box are gone. A transfer opens from the row's own side: picker = the
  other account, amount shown as |stored| with Money out / Money in (v1.15).
  Saving re-files: account → `save_transfer_pair(keep_sign, match_existing)`;
  category on a former transfer → `detach_transfer_partner` first.
- **Other Income / Other Expense** (`other_income`, `other_expense` category
  types). `ledger.pnl_section()` returns them separately; `pnl_bucket()` still
  maps them to income/expense, so net income and every existing total include
  them. `reports.pnl_rows()` rows carry `other: bool`; `pnl_section_label()` /
  `PNL_SECTION_ORDER` order exports; `reports_export.pnl_csv_rows()` adds "Net
  operating income" only when other rows exist. Use `ledger.is_income_type()` /
  `category_sign()` instead of comparing to "income". Schedule C maps by tax
  line only; a new Other Income category defaults to line 6.
- **Fixed Assets / Other Assets accounts** (`fixed assets`, `other assets` in
  `ACCOUNT_TYPES` and `ledger.ASSET_TYPES`) are hand-tracked balance-sheet
  assets: money moves in by transfer and the balance sheet lists the account's
  balance. They are NOT the Fixed Assets module: module assets are bought with an
  `asset`-type category (money out of the bank, off the P&L) and appear via
  `balance_sheet_extras()` as "Fixed assets & prepaids (cost)" less accumulated
  depreciation. The two never feed each other, so nothing is double-counted as
  long as the same purchase isn't recorded both ways (the account dialog warns
  about this). Cash flow puts transfers into a fixed-assets account under
  investing.
- **DateField** (`ui.widgets.parse_date_text`): "M/D" or "M-D" → this year,
  "M/D/YY" → 20YY, on every DateField.
- **Register selection.** `TransactionsPage._apply_filter(keep=txn)` re-selects
  by id → split group → transfer pair (same account) → account+date+payee, and
  scrolls it to the centre; without keep it scrolls to the bottom as before.
- Tests: `test_chart_of_accounts.py`, `test_other_pnl_types.py`,
  `test_rules_v14.py`, `test_transactions_screen.py`.

## v1.15 fixes (schema 13)

- **Money direction in the Transaction dialog.** `amount_spin` shows |amount|
  and `direction_combo` ("out"/"in") sets the stored sign: `get_data()` saves
  `entered * (-1 if out else 1)`. An existing entry's direction comes from its
  stored sign and is never changed by picking an account (`_direction_fixed`);
  a new / $0 entry follows `_natural_direction(picker value)` until the user
  picks a direction (`activated`). A negative entry flips it and is tidied on
  editingFinished. Transfers, loan payments, owner draws and splits use the
  same sign. Before, `stored * _category_sign(category)` made an uncategorized
  imported withdrawal (−50, sign +1) save as +50 once an expense was picked.
- **Splits / receipts keep an existing entry's direction.**
  `split_transaction` computes plain (category) lines with
  `_plain_split_amount`; if the stored entry's sign (`_stored_sign`, read before
  the old rows are deleted) disagrees with their total, those lines are negated
  (an imported refund split across expense accounts stays money in). The
  single-line receipt booking does the same. Context-menu re-file never changed
  the amount; there are no bulk re-categorize actions.
- **CSV/XLSX debits.** `debit_credit_amount(debit, credit)` = |credit| − |debit|
  in both `parse_spreadsheet` and `parse_qb_multi_account_excel`. The import
  hash still uses the pre-1.15 `credit - debit` so a file imported earlier
  doesn't duplicate on re-import. `parse_spreadsheet(reverse_signs=True)`
  negates a single Amount column (Import page checkbox).
- **Sign check** (Settings → Data Checks → `ui/dialogs/sign_check_dialog.py`):
  imported, non-transfer, non-split rows with amount > 0 on a spending type or
  < 0 on an income type. Per-row "Flip sign" and "Flip All Selected"; locked
  rows are disabled; read-only disables everything. Sets `sign_check_done`.
- **Uncategorized** = not a transfer and category_id empty or not an active
  category (`ledger.is_uncategorized`). Used by Apply to All, the Dashboard
  alert (which opens Transactions → "⚠ Uncategorized only") and the filter.
- **Transactions filters** apply on change: combos / checkbox at once; search
  and dates through a 300 ms single-shot `_filter_timer`. `_quiet` suppresses
  per-control reloads in `_clear_filter` / `show_account` /
  `show_uncategorized`; `refresh()` still blocks the combos' signals.
- **Pages refresh on show** (`MainWindow._nav_to` → `page.refresh()`, hub tabs
  too), so Transactions / Dashboard reflect Apply to All when opened.
- **Chart of Accounts tree.** `chart_rows` uses `_tree()`: any depth, a missing
  / inactive / cyclic parent shows the row at the top level — nothing is
  dropped. Entries carry `depth` and `parent`. Categories go in their root's
  group; an account nests only under a parent in its own group.
  `picker_entries` labels sub-rows "Parent → Child" (accounts add " · Group").
- **Moving.** Accounts and categories are kept ONE level deep. `move_category`
  / `move_account` refuse self, a sub-account as parent and (accounts) another
  group with ValueError, and move the row's own children under the new parent.
  A moved category takes the parent's type; its tax line / deduct % follow the
  parent when the type changes or it had none. `save_category` routes parent
  changes through `move_category` and a root's type change carries its subs.
  `save_account` writes `parent_id` only when the key is present and changed;
  a type change into another group detaches it (and mismatched subs).
  Accounts page: "Move…" button + right-click Move to / Make Top-Level;
  parent balance = own + subs (`rolled` in `_fill_chart`), group totals count
  each account once. AccountDialog(accounts=…) adds "Sub-account of".
- Tests: `test_import_signs.py`, `test_rules_v14.py` (ApplyAll*, DateCondition*),
  `test_transactions_filter_ui.py`, `test_subaccounts.py` (incl. 12 → 13).

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
- New pages: add to `_page_constructors()` and `utils/nav_config.py` (see the v1.13
  section). App modules load from `app/` at runtime, so they are not listed in
  `FinanceBook.spec` (only third-party libraries are).

---

## Owner

Adib Mazloom — `amazloom@fiyrpod.com`
Website: `adibmazloom.com`
GitHub: `github.com/Adeen924`
