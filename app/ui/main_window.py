"""Main application window — sidebar navigation + stacked content pages."""
import os
import re

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QLabel, QStackedWidget, QMessageBox, QPushButton,
                              QSizePolicy, QComboBox, QDialog)
from PyQt6.QtCore import Qt, QTimer

from ui.widgets import NavButton


NAV_ITEMS = [
    ("Dashboard",    "🏠"),
    ("Accounts",     "🏦"),
    ("Loans",        "🏛"),
    ("Transactions", "📋"),
    ("Transfers",    "↔"),
    ("Import",       "📥"),
    ("Rules",        "⚙"),
    ("Categories",   "🏷"),
    ("Reconcile",    "✓"),
    ("Reports",      "📊"),
]

SETTINGS_IDX = 10


def _page_constructors():
    # Explicit imports so PyInstaller can detect every module statically
    from ui.pages.dashboard    import DashboardPage
    from ui.pages.accounts     import AccountsPage
    from ui.pages.loans        import LoansPage
    from ui.pages.transactions import TransactionsPage
    from ui.pages.transfers    import TransfersPage
    from ui.pages.import_page  import ImportPage
    from ui.pages.rules        import RulesPage
    from ui.pages.categories   import CategoriesPage
    from ui.pages.reconcile    import ReconcilePage
    from ui.pages.reports      import ReportsPage
    from ui.pages.settings     import SettingsPage
    return [
        DashboardPage, AccountsPage, LoansPage, TransactionsPage, TransfersPage,
        ImportPage, RulesPage, CategoriesPage, ReconcilePage, ReportsPage,
        SettingsPage,
    ]


def _read_db_name(path: str) -> str:
    """Open a database file briefly to read its stored name."""
    try:
        from sheets.client import Database
        return Database(path).get_setting("database_name") or os.path.basename(path)
    except Exception:
        return os.path.basename(path)


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db       = db
        self._base_dir = os.path.dirname(os.path.abspath(db.db_path))
        from db_config import register_database
        register_database(os.path.abspath(db.db_path))
        self.setWindowTitle("FinanceBook")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)

        self._page_cache: dict[int, QWidget] = {}
        self._build()
        self._nav_to(0)

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = QWidget()
        self._sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        app_name = QLabel("FinanceBook")
        app_name.setObjectName("SidebarTitle")
        sb_layout.addWidget(app_name)

        # Database selector dropdown
        self._db_combo = QComboBox()
        self._db_combo.setObjectName("DbSelector")
        self._db_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._populate_db_combo()
        self._db_combo.activated.connect(self._on_db_selected)
        sb_layout.addWidget(self._db_combo)

        self._nav_buttons: list[NavButton] = []
        for i, (label, icon) in enumerate(NAV_ITEMS):
            btn = NavButton(label, icon, sidebar)
            btn.clicked.connect(lambda checked, idx=i: self._nav_to(idx))
            sb_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sb_layout.addStretch()

        settings_btn = NavButton("Settings", "🔧", sidebar)
        settings_btn.clicked.connect(lambda checked: self._nav_to(SETTINGS_IDX))
        sb_layout.addWidget(settings_btn)
        self._nav_buttons.append(settings_btn)

        reload_btn = QPushButton("⟳  Refresh")
        reload_btn.setObjectName("Secondary")
        reload_btn.setStyleSheet(
            "background:transparent; color:#90aac9; border:none; "
            "text-align:left; padding:8px 18px; font-size:12px;")
        reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reload_btn.clicked.connect(self._reload)
        sb_layout.addWidget(reload_btn)

        self._status_label = QLabel(f"● {os.path.basename(self.db.db_path)}")
        self._status_label.setObjectName("SidebarStatus")
        sb_layout.addWidget(self._status_label)

        root_layout.addWidget(sidebar)

        # ── Content area ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        root_layout.addWidget(self._stack)

    # ── database selector ─────────────────────────────────────────────────────

    def _populate_db_combo(self):
        self._db_combo.blockSignals(True)
        self._db_combo.clear()
        current_abs = os.path.abspath(self.db.db_path)

        from db_config import get_known_databases
        db_files = {os.path.abspath(p) for p in get_known_databases()}
        db_files.add(current_abs)
        sorted_files = sorted(db_files, key=lambda p: _read_db_name(p).lower())

        current_idx = 0
        for i, path in enumerate(sorted_files):
            name = _read_db_name(path)
            self._db_combo.addItem(name, path)
            if path == current_abs:
                current_idx = i
        self._db_combo.addItem("➕  New Database…", "__new__")
        self._db_combo.setCurrentIndex(current_idx)
        self._db_combo.blockSignals(False)

    def _on_db_selected(self, index: int):
        path = self._db_combo.itemData(index)
        if path == "__new__":
            self._create_new_database()
            return
        if path == os.path.abspath(self.db.db_path):
            return  # already on this one
        from sheets.client import Database
        self._switch_to(Database(path))

    def _create_new_database(self):
        from ui.database_dialog import DatabaseSetupDialog
        dialog = DatabaseSetupDialog(
            self,
            window_title="New Database",
            heading="Create a New Database",
            message="Give your new database a name and choose where to save it.",
            default_name="My Finances",
            default_location=self._base_dir,
            accept_label="Create")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._populate_db_combo()  # revert combo to current selection
            return

        name = dialog.database_name
        location = dialog.database_location
        os.makedirs(location, exist_ok=True)
        slug = re.sub(r"[^\w\s]", "", name).strip()
        slug = re.sub(r"\s+", "_", slug).lower() or "database"
        path = os.path.join(location, f"{slug}.db")
        if os.path.exists(path):
            i = 2
            while os.path.exists(os.path.join(location, f"{slug}_{i}.db")):
                i += 1
            path = os.path.join(location, f"{slug}_{i}.db")
        from sheets.client import Database
        new_db = Database(path)
        new_db.set_setting("database_name", name)
        self._switch_to(new_db)

    def _switch_to(self, new_db):
        self.db = new_db
        from db_config import register_database
        register_database(os.path.abspath(new_db.db_path), make_active=True)
        for i in list(self._page_cache):
            w = self._page_cache.pop(i)
            self._stack.removeWidget(w)
            w.deleteLater()
        self._populate_db_combo()
        self._status_label.setText(f"● {os.path.basename(new_db.db_path)}")
        self._nav_to(0)

    # ── navigation ────────────────────────────────────────────────────────────

    def _load_page(self, index: int) -> QWidget:
        if index in self._page_cache:
            return self._page_cache[index]
        cls  = _page_constructors()[index]
        page = cls(self.db)
        # Let pages request cross-page navigation (e.g. click an account name to
        # jump to its transactions).
        page._main_window = self
        self._stack.addWidget(page)
        self._page_cache[index] = page
        return page

    def _page_index(self, name: str):
        """Nav index for a page by its sidebar name ('Settings' included)."""
        if name == "Settings":
            return SETTINGS_IDX
        return next((i for i, (n, _) in enumerate(NAV_ITEMS) if n == name), None)

    def start_tour(self):
        """Launch the interactive guided tour (coach marks over real buttons)."""
        from ui.guided_tour import GuidedTour
        self._tour = GuidedTour(self)
        self._tour.start()

    def open_account_transactions(self, account_id: str):
        """Jump to the Transactions page filtered to one account (recent first)."""
        idx = self._page_index("Transactions")
        if idx is None:
            return
        self._nav_to(idx)
        page = self._page_cache.get(idx)
        if page and hasattr(page, "show_account"):
            page.show_account(account_id)

    def _nav_to(self, index: int):
        for i, btn in enumerate(self._nav_buttons):
            btn.set_active(i == index)
        page = self._load_page(index)
        self._stack.setCurrentWidget(page)
        if hasattr(page, "refresh"):
            page.refresh()

    def _reload(self):
        try:
            self.db.reload()
            current = self._stack.currentWidget()
            if hasattr(current, "refresh"):
                current.refresh()
            current_idx = next(
                (i for i, w in self._page_cache.items() if w is current), None)
            for i in list(self._page_cache):
                if i != current_idx:
                    w = self._page_cache.pop(i)
                    self._stack.removeWidget(w)
                    w.deleteLater()
            self._status_label.setText("● Refreshed")
            QTimer.singleShot(3000,
                lambda: self._status_label.setText(f"● {os.path.basename(self.db.db_path)}"))
        except Exception as e:
            QMessageBox.warning(self, "Reload Failed", str(e))
