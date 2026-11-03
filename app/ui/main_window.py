"""Main application window — sidebar navigation + stacked content pages."""
import os
import re

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QLabel, QStackedWidget, QMessageBox, QPushButton,
                              QSizePolicy, QComboBox, QDialog, QFrame)
from PyQt6.QtCore import Qt, QTimer, QSize, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPixmap, QColor, QLinearGradient, QRadialGradient

from ui.widgets import NavButton, GlassCard, brand_pixmap, line_icon
from ui import styles as S
from ui.styles import MUTED


NAV_ITEMS = [
    ("Dashboard",    "dashboard"),
    ("Accounts",     "accounts"),
    ("Loans",        "loans"),
    ("Transactions", "transactions"),
    ("Transfers",    "transfers"),
    ("Import",       "import"),
    ("Rules",        "rules"),
    ("Categories",   "categories"),
    ("Reconcile",    "reconcile"),
    ("Reports",      "reports"),
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


class GlassBackground(QWidget):
    """
    Window backdrop: a soft light-blue gradient with a few large, blurred
    sky / blue / lavender blobs, so the translucent cards above it have depth.
    Rendered once per window size into a cached pixmap.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: QPixmap | None = None

    def resizeEvent(self, event):
        self._cache = None
        super().resizeEvent(event)

    def _render(self) -> QPixmap:
        w, h = max(self.width(), 1), max(self.height(), 1)
        ratio = self.devicePixelRatioF()
        pm = QPixmap(int(w * ratio), int(h * ratio))
        pm.setDevicePixelRatio(ratio)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0.0, QColor(S.BG_TOP))
        g.setColorAt(0.5, QColor(S.BG_MID))
        g.setColorAt(1.0, QColor(S.BG_BOTTOM))
        p.fillRect(QRectF(0, 0, w, h), g)

        def blob(cx, cy, radius, rgb, alpha):
            rg = QRadialGradient(QPointF(cx, cy), radius)
            rg.setColorAt(0.0, QColor(*rgb, alpha))
            rg.setColorAt(0.55, QColor(*rgb, int(alpha * 0.45)))
            rg.setColorAt(1.0, QColor(*rgb, 0))
            p.fillRect(QRectF(0, 0, w, h), rg)

        span = max(w, h)
        blob(w * 0.88, h * 0.06, span * 0.42, S.BLOB_SKY, 120)
        blob(w * 0.12, h * 0.95, span * 0.40, S.BLOB_LAVENDER, 105)
        blob(w * 0.45, h * 0.50, span * 0.30, S.BLOB_BLUE, 60)
        p.end()
        return pm

    def paintEvent(self, event):
        if self._cache is None or self._cache.deviceIndependentSize().toSize() != self.size():
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.end()


def _clear_scroll_backgrounds(widget: QWidget):
    """Let the glass backdrop show through page-level scroll areas."""
    from PyQt6.QtWidgets import QScrollArea
    for sa in widget.findChildren(QScrollArea):
        sa.viewport().setAutoFillBackground(False)
        if sa.widget() is not None:
            sa.widget().setAutoFillBackground(False)


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
        root = GlassBackground()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 6, 4)
        root_layout.setSpacing(0)

        # ── Sidebar: a floating glass panel ─────────────────────────────────
        sidebar = GlassCard(radius=22)
        self._sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(228)
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(12, 16, 12, 10)
        sb_layout.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 10)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(brand_pixmap(32))
        logo.setFixedSize(32, 32)
        brand.addWidget(logo)
        app_name = QLabel("FinanceBook")
        app_name.setObjectName("SidebarTitle")
        brand.addWidget(app_name)
        brand.addStretch()
        sb_layout.addLayout(brand)

        db_lbl = QLabel("DATABASE")
        db_lbl.setObjectName("SidebarSection")
        sb_layout.addWidget(db_lbl)

        # Database selector dropdown
        self._db_combo = QComboBox()
        self._db_combo.setObjectName("DbSelector")
        self._db_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._populate_db_combo()
        self._db_combo.activated.connect(self._on_db_selected)
        sb_layout.addWidget(self._db_combo)

        menu_lbl = QLabel("MENU")
        menu_lbl.setObjectName("SidebarSection")
        sb_layout.addWidget(menu_lbl)

        self._nav_buttons: list[NavButton] = []
        for i, (label, icon) in enumerate(NAV_ITEMS):
            btn = NavButton(label, icon, sidebar)
            btn.clicked.connect(lambda checked, idx=i: self._nav_to(idx))
            sb_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sb_layout.addStretch()

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(divider)
        sb_layout.addSpacing(6)

        settings_btn = NavButton("Settings", "settings", sidebar)
        settings_btn.clicked.connect(lambda checked: self._nav_to(SETTINGS_IDX))
        sb_layout.addWidget(settings_btn)
        self._nav_buttons.append(settings_btn)

        reload_btn = QPushButton("  Refresh")
        reload_btn.setObjectName("SidebarLink")
        reload_btn.setIcon(line_icon("refresh", MUTED, 16))
        reload_btn.setIconSize(QSize(16, 16))
        reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reload_btn.clicked.connect(self._reload)
        sb_layout.addWidget(reload_btn)

        self._status_label = QLabel(f"● {os.path.basename(self.db.db_path)}")
        self._status_label.setObjectName("SidebarStatus")
        sb_layout.addWidget(self._status_label)

        root_layout.addWidget(sidebar)

        # ── Content area ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        root_layout.addWidget(self._stack, 1)

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
        _clear_scroll_backgrounds(page)
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
