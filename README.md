# FinanceBook

A native **desktop personal finance application** — PyQt6 + SQLite. Think
QuickBooks Lite: multiple bank accounts, transaction import, categories,
reconciliation, P&L / Balance Sheet reports, auto-categorization rules, loan
tracking, and multiple separate databases (personal / company / property).

Runs on Windows. Data lives in a local `.db` file. No web server, no cloud
account, no internet required (except the optional update check).

## For users

Download the latest **`FinanceBook-x.y.z.zip`** from the
[Releases page](https://github.com/Adeen924/FinanceBook/releases/latest),
extract it anywhere, and double-click **`FinanceBook.exe`**. Nothing to install —
Python and every dependency are bundled inside the app.

When a new version is out, FinanceBook shows an **"Update available"** banner on
startup. Click **Install Update** once and the app downloads it, restarts, and
reopens on the new version. Your data is never touched — it lives separately at
`%APPDATA%\FinanceBook\finances.db`.

## For developers

### Run from source
```
double-click run.bat
```
Creates `.venv`, installs `requirements.txt`, and runs `app/main.py`. In source
mode the database lives next to `app/main.py`.

### Project layout
```
launcher.py            Thin frozen entry point. Bundles Python + all deps;
                       its only job is to load and run app/.
FinanceBook.spec       PyInstaller config — freezes ONLY launcher.py.
build.bat              Local build -> dist\ (two zips). CI normally does this.
run.bat                Dev launcher (venv + run app\main.py)
requirements.txt       Runtime deps
.github/workflows/
  release.yml          Tag-push -> Windows build -> GitHub Release (see below)

app/                   The REAL application code (plain, swappable files)
  VERSION              Plain-text version marker, e.g. "1.0.3"
  main.py              Entry point
  updater.py           One-click in-app updater (GitHub Releases API)
  sheets/client.py     SQLite backend (all DB logic)
  parsers/             QFX/QBO, CSV/XLSX, IIF parsers
  utils/loan.py        Amortization math
  ui/                  PyQt6 windows, pages, dialogs
```

### How updates work
The shipped product is a **thin launcher `.exe`** sitting next to a plain-files
**`app/`** folder. A routine update just replaces `app/` — the locked `.exe` is
never touched, so there's no self-overwrite problem and no reinstall. Version
checks read **GitHub Releases** (no hand-maintained `version.json`). Full design
in [`UPDATE_PLAN.md`](UPDATE_PLAN.md).

### Cutting a release
Two ways, both run the same Windows build and publish a GitHub Release with the
app-payload zip (one-click updates) and the full zip (first installs):

```
# 1. Tag push
git tag v1.0.3
git push origin v1.0.3

# 2. Or: GitHub -> Actions -> "Release" -> Run workflow -> type version + notes
```
The workflow auto-writes the version into `app/VERSION` from the tag — you set
the version in exactly one place.
```

## License

Private project. Owner: Adib Mazloom.
