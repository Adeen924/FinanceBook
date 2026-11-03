# FinanceBook

**A simple, private bookkeeping app for your own money — right on your Windows PC.**

FinanceBook helps you keep the books for your household, rental properties, or a
small business without a monthly subscription, without an online account, and
without handing your finances to anyone else. Everything lives in a single file
on your own computer.

Think of it as a lightweight alternative to QuickBooks: track several accounts,
import your bank and credit-card activity, sort transactions into categories,
reconcile against your statements, and print clean Profit & Loss and Balance
Sheet reports.

## New: business books (v1.1)

FinanceBook can now keep the books for a **small business** (sole proprietor or
single-member LLC), not just personal money. Create a new database and choose
**Business**, or switch an existing one in **Settings → Book Type**.

- **No business bank account yet? No problem.** Record company expenses you
  pay out of your own pocket as either a **capital contribution** (your
  investment) or money the **company owes you**. Pay yourself back later with
  one click (**Reimburse Owner**), and switch to a real business account
  whenever you open one.
- **Owner Investment page** — how much you've put in, been repaid, taken out,
  and are still owed, with every owner-paid expense listed and a CSV export.
- **Balance Sheet with an Equity section** — owner contributions, draws,
  retained earnings and this year's profit, and it always balances.
- **Schedule C categories** — business categories mapped to IRS Schedule C
  lines (meals flagged 50%), including a full Advertising & Marketing group.
- **Automatic backups** — a daily backup next to your database (keeps the last
  10), a backup before every upgrade, and one-click backup/restore in Settings.

- **Receipts (v1.2)** — drag in photos, PDFs, emailed receipts (.eml), saved web
  pages or text. FinanceBook reads the vendor, date, tax, tip and line items,
  checks they add up, and lets you pick a category, project and business/personal
  for each item before saving it as one split transaction with the receipt
  attached. It remembers each vendor's usual category, and you can search every
  item you've ever bought ("what did I spend on printer paper?").
- **Optional AI receipt reading** — for photos and scanned PDFs, paste your own
  Anthropic API key in Settings and click **Read with AI**. Off by default; the
  receipt file is only sent when you click.
- **Attach receipts to any transaction** — including imported bank lines (look
  for the 📎), then itemize that bank charge from its receipt.
- **Fixed assets & depreciation (v1.3)** — keep a register of machines,
  vehicles, equipment and prepaid expenses (insurance, licenses). FinanceBook
  calculates depreciation automatically — straight-line, declining balance,
  units of production (machine hours), Section 179 / bonus, or MACRS — shows it
  on the P&L, shows assets at book value on the Balance Sheet, and works out the
  gain or loss when you sell one. Add an asset straight from a receipt line or
  a transaction.
- **Invoices, bills & contacts (v1.4)** — keep vendors and customers (with
  payment terms and 1099 flags), send numbered invoices as PDFs, enter bills,
  and record payments (each becomes a transaction automatically; sales tax on
  invoices goes to a Sales Tax Payable liability). A/R and A/P aging show
  what's overdue, the dashboard shows what you're owed and what you owe, and
  imported bank lines are matched to vendors automatically.

Tax-related labels are bookkeeping aids, not tax advice — confirm treatment with
your accountant.

## New look (v1.0.22)

FinanceBook has a fresh, light "glass" design: translucent cards over a soft
blue backdrop, a floating sidebar, and a redesigned dashboard showing net
worth, assets, liabilities and this month's cash flow at a glance, plus an
income-vs-expenses chart, a spending-by-category donut, recent transactions and
your accounts. Your data and how everything works are unchanged — just update
from the in-app banner.

## What you can do with it

- **Track all your accounts in one place** — checking, savings, credit cards,
  loans, and more, each with its own running balance.
- **Import from your bank** — drop in the files your bank exports
  (`.qfx` / `.qbo`, `.ofx`, `.csv`, `.xlsx`, or QuickBooks `.iif`) and
  FinanceBook adds the transactions for you, skipping duplicates.
- **Organize spending** — assign transactions to categories and subcategories so
  you can see where the money actually goes.
- **Automate the busywork** — set up rules that categorize recurring
  transactions automatically as they come in.
- **Reconcile against statements** — tick off transactions and confirm your
  records match the bank, the same way an accountant would.
- **See the whole picture** — Profit & Loss and Balance Sheet reports you can
  print or save, plus a dashboard summarizing your accounts.
- **Track loans** — record a loan and FinanceBook works out the payment
  schedule, splitting each payment into principal and interest.
- **Keep separate books** — run completely separate databases for, say,
  personal, company, and property finances, and switch between them. Each one
  is either a **Personal** or a **Business** book.
- **Run a small business** — track money you put in or take out as the owner
  (even with no business bank account), see a proper Balance Sheet with an
  Equity section, and use categories mapped to Schedule C.
- **Stay backed up** — automatic daily backups next to each database, plus
  one-click backup and restore.
- **Invoice customers and track bills** — numbered PDF invoices, bills,
  payments, and A/R / A/P aging.
- **Track equipment and depreciation** — an asset register with automatic
  depreciation schedules and gain/loss on sale.
- **Keep receipts with your books** — attach receipt files to transactions and
  turn itemized receipts into split transactions automatically.

## Your data stays yours

FinanceBook runs entirely on your computer. There is **no cloud account, no sign
up, and no internet connection required** to use it. Your finances are saved in a
local database file that only you can see — you even choose where each database
is stored. The only thing that ever goes online is an optional check for app
updates.

## Getting started

1. Download the latest **`FinanceBook-x.y.z.zip`** from the
   [Releases page](https://github.com/Adeen924/FinanceBook/releases/latest).
2. Extract it anywhere you like (for example, your Desktop or Documents folder).
3. Double-click **`FinanceBook.exe`**. There's nothing to install — everything
   the app needs is bundled inside.

The first time you open it, a short guided tour walks you through setting up your
first account and importing transactions.

## Staying up to date

When a new version is available, FinanceBook shows an **"Update available"**
banner when you start it. Click **Install Update** once — the app downloads the
update, restarts, and reopens on the new version. **Your data is never touched**
during an update; it lives separately from the app itself.

## Questions or problems

Found a bug or have an idea? Open an issue on the
[Issues page](https://github.com/Adeen924/FinanceBook/issues).

---

<sub>Building from source or contributing? Developer and build documentation lives
in [`HANDOFF.md`](HANDOFF.md) and [`UPDATE_PLAN.md`](UPDATE_PLAN.md).</sub>

## License

Private project. Owner: Adib Mazloom.
