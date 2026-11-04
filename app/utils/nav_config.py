"""
What the sidebar shows — Qt-free so it can be unit-tested.

Pages keep their names as stable IDs (the guided tour, dashboard links and
mw.page("Import") all use them). For a Business book this module groups those
pages into sidebar items, some of which are "hubs" whose pages appear as tabs,
and leaves out the optional modules the user has turned off. Personal books get
exactly the flat list they always had.

Hiding a module only changes navigation: its data, and everything computed
from it (reports, balance sheet, COGS, depreciation, liabilities), is untouched.

The choice is stored in the database's settings table under NAV_SETTING_KEY,
so it travels with the database (e.g. to another laptop via OneDrive). Older
app versions simply ignore the key.
"""
import json
from typing import NamedTuple

NAV_SETTING_KEY = "nav_modules"
_FORMAT_VERSION = 1


class Module(NamedTuple):
    key: str
    label: str
    description: str
    pages: tuple            # page names this module shows


# Order is the order Settings lists them in.
MODULES = (
    Module("receipts", "Receipts",
           "Scan or drop in receipts; FinanceBook reads them and books split transactions.",
           ("Receipts",)),
    Module("invoices", "Invoices & Bills",
           "Numbered invoices, vendor bills, payments and A/R / A/P aging.",
           ("Invoices & Bills",)),
    Module("owner", "Owner Investment",
           "Track money you put in or take out, and expenses you paid personally.",
           ("Owner Investment",)),
    Module("taxes", "Taxes",
           "Estimated taxes, sales tax, mileage log, home office and the year-end package.",
           ("Taxes",)),
    Module("assets", "Fixed Assets",
           "Equipment and vehicles with depreciation (straight-line, Section 179, MACRS…).",
           ("Assets",)),
    Module("loans", "Loans",
           "Loan balances and amortization schedules, shown as a tab under Accounts.",
           ("Loans",)),
    Module("inventory", "Inventory",
           "Stock you sell: quantities, FIFO / average costing and cost of goods sold.",
           ("Inventory",)),
    Module("payroll", "People & Payroll",
           "Employees, time, payroll runs and 1099 contractors.",
           ("People & Payroll",)),
)
MODULE_KEYS = tuple(m.key for m in MODULES)
MODULES_BY_KEY = {m.key: m for m in MODULES}
PAGE_MODULE = {p: m.key for m in MODULES for p in m.pages}


class Preset(NamedTuple):
    key: str
    label: str
    description: str
    modules: frozenset


PRESETS = (
    Preset("simple", "Simple",
           "Freelancer or sole proprietor: receipts, invoices, owner money and taxes.",
           frozenset({"receipts", "invoices", "owner", "taxes"})),
    Preset("standard", "Standard",
           "Most small businesses: adds fixed assets and loans.",
           frozenset({"receipts", "invoices", "owner", "taxes", "assets", "loans"})),
    Preset("full", "Full",
           "Everything, including inventory and payroll.",
           frozenset(MODULE_KEYS)),
)
PRESETS_BY_KEY = {p.key: p for p in PRESETS}
DEFAULT_NEW_PRESET = "standard"
ALL_MODULES = frozenset(MODULE_KEYS)

# Line types in the receipt dialog that need a module (the rest are always there).
RECEIPT_KIND_MODULE = {"asset": "assets", "inventory": "inventory"}

# Contacts are used well beyond invoicing (the Vendor / customer field on every
# business transaction, inventory vendors, 1099 contractors, the By Vendor
# report), so they are never hidden: a tab of Invoices & Bills when that module
# is on, otherwise their own sidebar item.
CONTACTS_HOST = "invoices"


# ── stored preference ────────────────────────────────────────────────────────

def _parse(raw) -> dict | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), list):
        return None
    return data


def parse_modules(raw) -> frozenset:
    """
    Enabled modules from the stored setting. Missing, empty or corrupt → all
    (so an existing database never loses a page unexpectedly). Unknown names
    (from a newer version) are ignored; modules this version knows but the
    writer didn't (added in a later release) default to on.
    """
    data = _parse(raw)
    if data is None:
        return ALL_MODULES
    enabled = {m for m in data["enabled"] if isinstance(m, str)}
    known = data.get("known")
    known = ({m for m in known if isinstance(m, str)} if isinstance(known, list)
             else set(MODULE_KEYS))
    return frozenset((enabled & ALL_MODULES) | (ALL_MODULES - known))


def dump_modules(enabled, previous_raw: str = "") -> str:
    """JSON for the setting. Keeps module names this version doesn't know (written
    by a newer version on the other laptop) so they survive a round trip."""
    enabled = set(enabled) & ALL_MODULES
    prev = _parse(previous_raw) or {}
    foreign_on = {m for m in prev.get("enabled", []) if isinstance(m, str)} - ALL_MODULES
    prev_known = prev.get("known") if isinstance(prev.get("known"), list) else []
    foreign_known = {m for m in prev_known if isinstance(m, str)} - ALL_MODULES
    return json.dumps({
        "version": _FORMAT_VERSION,
        "preset": preset_for(enabled),
        "enabled": sorted(enabled | foreign_on),
        "known": sorted(ALL_MODULES | foreign_known | foreign_on),
    })


def enabled_modules(db) -> frozenset:
    """Modules on for this database (all of them for a personal book, which has
    no optional modules)."""
    if not db.is_business():
        return ALL_MODULES
    try:
        return parse_modules(db.get_setting(NAV_SETTING_KEY))
    except Exception:
        return ALL_MODULES


def save_modules(db, enabled):
    db.set_setting(NAV_SETTING_KEY, dump_modules(enabled, db.get_setting(NAV_SETTING_KEY)))


def preset_for(enabled) -> str:
    """The preset these modules match, or 'custom'."""
    enabled = frozenset(enabled) & ALL_MODULES
    for p in PRESETS:
        if p.modules == enabled:
            return p.key
    return "custom"


def preset_modules(key: str) -> frozenset:
    p = PRESETS_BY_KEY.get(key)
    return p.modules if p else ALL_MODULES


# ── layout ───────────────────────────────────────────────────────────────────

class NavItem(NamedTuple):
    id: str                 # nav-button key; a page name except for pure hubs
    label: str              # sidebar text
    icon: str
    tabs: tuple             # ((page name, tab label), …); one entry = a plain page

    @property
    def pages(self) -> tuple:
        return tuple(p for p, _ in self.tabs)

    @property
    def is_hub(self) -> bool:
        return len(self.tabs) > 1


def _plain(name, icon, label=None):
    return NavItem(name, label or name, icon, ((name, name),))


# Personal books: exactly the list they have always had.
_PERSONAL = (
    ("OVERVIEW", (_plain("Dashboard", "dashboard"),)),
    ("MONEY", (
        _plain("Accounts", "accounts"),
        _plain("Transactions", "transactions"),
        _plain("Transfers", "transfers"),
        _plain("Import", "import"),
        _plain("Reconcile", "reconcile"),
        _plain("Loans", "loans"),
        _plain("Recurring", "calendar"),
        _plain("Rules", "rules"),
        _plain("Categories", "categories"),
    )),
    ("REPORTS", (_plain("Reports", "reports"),)),
)

# Every page the app can show (Settings lives in the sidebar footer).
ALL_PAGES = (
    "Dashboard", "Accounts", "Loans", "Transactions", "Import", "Reconcile",
    "Transfers", "Recurring", "Receipts", "Invoices & Bills", "Contacts",
    "Inventory", "Assets", "People & Payroll", "Owner Investment", "Taxes",
    "Reports", "Categories", "Rules", "Settings",
)


def visible_sections(book_type: str, enabled=ALL_MODULES) -> list:
    """[(header, [NavItem, …]), …] for the sidebar. Hubs show only the tabs
    whose module is on."""
    if book_type != "business":
        return [(h, list(items)) for h, items in _PERSONAL]
    on = frozenset(enabled) & ALL_MODULES

    accounts_tabs = [("Accounts", "Accounts")]
    if "loans" in on:
        accounts_tabs.append(("Loans", "Loans"))
    money = [
        NavItem("Accounts", "Accounts", "accounts", tuple(accounts_tabs)),
        NavItem("Transactions", "Transactions", "transactions", (
            ("Transactions", "Transactions"), ("Import", "Import"),
            ("Reconcile", "Reconcile"), ("Transfers", "Transfers"),
            ("Recurring", "Recurring"))),
    ]

    business = []
    if "receipts" in on:
        business.append(_plain("Receipts", "receipt"))
    if CONTACTS_HOST in on:
        business.append(NavItem("Invoices & Bills", "Invoices & Bills", "invoice", (
            ("Invoices & Bills", "Invoices & Bills"), ("Contacts", "Contacts"))))
    else:
        business.append(_plain("Contacts", "contacts"))
    if "inventory" in on:
        business.append(_plain("Inventory", "inventory"))
    if "assets" in on:
        business.append(_plain("Assets", "asset", "Fixed Assets"))
    if "payroll" in on:
        business.append(_plain("People & Payroll", "people"))
    if "owner" in on:
        business.append(_plain("Owner Investment", "owner"))
    if "taxes" in on:
        business.append(_plain("Taxes", "taxes"))

    return [
        ("OVERVIEW", [_plain("Dashboard", "dashboard")]),
        ("MONEY", money),
        ("BUSINESS", business),
        ("REPORTS", [
            _plain("Reports", "reports"),
            NavItem("Categories & Rules", "Categories & Rules", "categories", (
                ("Categories", "Categories"), ("Rules", "Rules"))),
        ]),
    ]


def visible_items(book_type: str, enabled=ALL_MODULES) -> list:
    """Flat list of the sidebar's NavItems (Settings, in the footer, excluded)."""
    return [it for _, items in visible_sections(book_type, enabled) for it in items]


def locate(name: str, book_type: str, enabled=ALL_MODULES):
    """(NavItem, page name) for a page name or sidebar item id, or None when it
    isn't shown in this book. Settings is always (None, "Settings")."""
    if name == "Settings":
        return None, "Settings"
    for it in visible_items(book_type, enabled):
        if name in it.pages:
            return it, name
    for it in visible_items(book_type, enabled):
        if name == it.id:
            return it, it.pages[0]
    return None


def module_for_page(name: str):
    """The optional module that shows this page, or None if it's always on."""
    return PAGE_MODULE.get(name)


def receipt_line_kinds(book_type: str, enabled=ALL_MODULES, keep=()) -> list:
    """Receipt line types offered: Expense always; Fixed asset / Inventory in a
    Business book when their module is on. Kinds in `keep` (already used on
    this receipt's lines) stay available so turning a module off never silently
    changes a saved line."""
    kinds = [("expense", "Expense")]
    if book_type == "business":
        on = frozenset(enabled)
        for key, label in (("asset", "Fixed asset"), ("inventory", "Inventory")):
            if RECEIPT_KIND_MODULE[key] in on or key in keep:
                kinds.append((key, label))
    return kinds
