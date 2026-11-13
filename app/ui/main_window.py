"""Main application window — sidebar navigation + stacked content pages."""
import os
import re
import time

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QLabel, QStackedWidget, QMessageBox, QPushButton,
                              QSizePolicy, QComboBox, QDialog, QFrame, QButtonGroup)
from PyQt6.QtCore import Qt, QTimer, QSize, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPixmap, QColor, QLinearGradient, QRadialGradient

from ui.widgets import NavButton, GlassCard, brand_pixmap, line_icon
from ui import styles as S
from ui.styles import MUTED


# What the sidebar shows (and how Business pages group into tabbed hubs) is
# decided in utils.nav_config; page names stay the stable IDs used everywhere.
from utils import nav_config as NC


def _page_constructors() -> dict:
    # Explicit imports so static analysis sees every page module.
    from ui.pages.dashboard    import DashboardPage
    from ui.pages.accounts     import AccountsPage
    from ui.pages.loans        import LoansPage
    from ui.pages.transactions import TransactionsPage
    from ui.pages.transfers    import TransfersPage
    from ui.pages.import_page  import ImportPage
    from ui.pages.rules        import RulesPage
    from ui.pages.reconcile    import ReconcilePage
    from ui.pages.reports      import ReportsPage
    from ui.pages.settings     import SettingsPage
    from ui.pages.owner        import OwnerInvestmentPage
    from ui.pages.receipts     import ReceiptsPage
    from ui.pages.assets       import AssetsPage
    from ui.pages.invoices     import InvoicesPage
    from ui.pages.contacts     import ContactsPage
    from ui.pages.inventory    import InventoryPage
    from ui.pages.payroll      import PayrollPage
    from ui.pages.taxes        import TaxesPage
    from ui.pages.recurring    import RecurringPage
    return {
        "Dashboard": DashboardPage, "Accounts": AccountsPage, "Loans": LoansPage,
        "Transactions": TransactionsPage, "Transfers": TransfersPage,
        "Import": ImportPage, "Rules": RulesPage,
        "Reconcile": ReconcilePage, "Reports": ReportsPage,
        "Settings": SettingsPage, "Owner Investment": OwnerInvestmentPage,
        "Receipts": ReceiptsPage, "Assets": AssetsPage,
        "Invoices & Bills": InvoicesPage, "Contacts": ContactsPage,
        "Inventory": InventoryPage, "People & Payroll": PayrollPage,
        "Taxes": TaxesPage, "Recurring": RecurringPage,
    }


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


class HubPage(QWidget):
    """A Business sidebar item that groups several pages: a row of tabs above a
    stack holding the real page widgets, which are created lazily by the main
    window (so mw.page("Import") is still the ImportPage itself)."""
    def __init__(self, item, on_tab, parent=None):
        super().__init__(parent)
        self.item = item
        self._buttons: dict[str, QPushButton] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("HubBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(28, 10, 28, 0)
        row.setSpacing(0)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for page, label in item.tabs:
            b = QPushButton(label.replace("&", "&&"))
            b.setObjectName("HubTab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=page: on_tab(p))
            group.addButton(b)
            row.addWidget(b)
            self._buttons[page] = b
        row.addStretch()
        lay.addWidget(bar)
        self._stack = QStackedWidget()
        lay.addWidget(self._stack, 1)

    def tab_labels(self) -> list[str]:
        return [b.text().replace("&&", "&") for b in self._buttons.values()]

    def current_tab(self) -> str:
        return next((p for p, b in self._buttons.items() if b.isChecked()), "")

    def holds(self, widget) -> bool:
        return self._stack.indexOf(widget) >= 0

    def show_page(self, name: str, widget: QWidget):
        if not self.holds(widget):
            self._stack.addWidget(widget)
        self._stack.setCurrentWidget(widget)
        self._buttons[name].setChecked(True)

    def release(self, widget: QWidget):
        """Take a page out without deleting it (it moves to a rebuilt hub)."""
        self._stack.removeWidget(widget)
        widget.setParent(None)


def _read_db_name(path: str) -> str:
    """Read a database's stored name read-only — listing a database in the
    dropdown must never migrate (or back up) it."""
    from sheets.backup import read_setting_readonly
    return read_setting_readonly(path, "database_name") or os.path.basename(path)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class MainWindow(QMainWindow):
    def __init__(self, db, lock=None):
        """lock: the db_lock.DbLock held for db (None when read-only or unlockable)."""
        super().__init__()
        self.db       = db
        self._lock    = lock
        self._base_dir = os.path.dirname(os.path.abspath(db.db_path))
        from db_config import register_database
        register_database(os.path.abspath(db.db_path))
        self.setWindowTitle("FinanceBook")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)

        self._page_cache: dict[str, QWidget] = {}
        self._hub_cache: dict[str, HubPage] = {}
        self._modules = NC.ALL_MODULES
        self._nav_buttons: dict[str, NavButton] = {}
        self._current_page = ""
        self._hb_busy = False
        import db_lock
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(db_lock.HEARTBEAT_SECONDS * 1000)
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(
            lambda: self._status_label.setText(f"● {os.path.basename(self.db.db_path)}"))
        self._build()
        self._update_lock_state()
        self._nav_to("Dashboard")

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

        self._book_badge = QLabel("")
        self._book_badge.setObjectName("BookBadge")
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(6, 6, 0, 0)
        badge_row.addWidget(self._book_badge)
        badge_row.addStretch()
        sb_layout.addLayout(badge_row)

        # The grouped page list scrolls on short windows instead of squashing.
        from PyQt6.QtWidgets import QScrollArea
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_scroll.viewport().setAutoFillBackground(False)
        sb_layout.addWidget(self._nav_scroll, 1)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        sb_layout.addWidget(divider)
        sb_layout.addSpacing(6)

        settings_btn = NavButton("Settings", "settings", sidebar)
        settings_btn.clicked.connect(lambda checked: self._nav_to("Settings"))
        sb_layout.addWidget(settings_btn)
        self._settings_btn = settings_btn
        self._build_nav()

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
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(8, 0, 0, 0)
        content_lay.setSpacing(6)
        content_lay.addWidget(self._build_read_only_banner())
        self._stack = QStackedWidget()
        content_lay.addWidget(self._stack, 1)
        root_layout.addWidget(content, 1)

    def _build_read_only_banner(self) -> QFrame:
        self._ro_banner = QFrame()
        self._ro_banner.setObjectName("ReadOnlyBanner")
        row = QHBoxLayout(self._ro_banner)
        row.setContentsMargins(14, 8, 10, 8)
        self._ro_label = QLabel("")
        self._ro_label.setWordWrap(True)
        row.addWidget(self._ro_label, 1)
        retry = QPushButton("Open for Editing")
        retry.setObjectName("Secondary")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.clicked.connect(lambda: self.open_database(self.db.db_path))
        row.addWidget(retry)
        self._ro_banner.hide()
        return self._ro_banner

    # ── lock / read-only state ────────────────────────────────────────────────

    def _update_lock_state(self):
        """Show the read-only banner and run the heartbeat to match self.db/_lock."""
        import db_lock
        if self.db.read_only:
            info = db_lock.read_lock(self.db.db_path) or {}
            where = (f" — it's open on {info['machine']}"
                     if info.get("machine") and not info.get("corrupt") else "")
            self._ro_label.setText(
                f"<b>Read-only</b>{where}. You can look, but changes can't be saved. "
                "When it's closed there (and OneDrive has synced), choose Open for Editing.")
            self._ro_banner.show()
        else:
            self._ro_banner.hide()
        if self._lock is not None and self._lock.owned:
            self._heartbeat.start()
        else:
            self._heartbeat.stop()
        self.setWindowTitle("FinanceBook  (read-only)" if self.db.read_only else "FinanceBook")

    def _on_heartbeat(self):
        import db_lock
        if self._hb_busy or self._lock is None:
            return
        self._hb_busy = True
        try:
            gap = time.time() - self._lock.last_beat
            if gap > db_lock.STALE_AFTER_SECONDS:
                # The computer slept (or the app hung) long enough for the lock
                # to look abandoned; the other laptop may have opened the file
                # and its lock may not have synced here yet.
                if not self._lock.still_ours():
                    self._lost_lock()
                    return
                if QMessageBox.question(
                        self, "Welcome Back",
                        f"FinanceBook was inactive for {int(gap // 60)} minutes, so its lock on "
                        "this database expired.\n\nIf you opened this database on another "
                        "computer in the meantime, choose No — this window switches to "
                        "read-only so the two don't overwrite each other.\n\n"
                        "Keep editing here?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
                    self._go_read_only()
                    return
            if not self._lock.heartbeat():
                self._lost_lock()
        finally:
            self._hb_busy = False

    def _go_read_only(self):
        """Stop writing to the current database. Pages share self.db, so the
        flag takes effect everywhere at once."""
        if self._lock is not None:
            self._lock.release()
        self._lock = None
        self.db.read_only = True
        self._update_lock_state()

    def _lost_lock(self):
        import db_lock
        self._lock = None
        self.db.read_only = True
        self._update_lock_state()
        info = db_lock.read_lock(self.db.db_path) or {}
        who = (db_lock.describe(info) if not info.get("corrupt") and info
               else "It is now in use somewhere else")
        QMessageBox.warning(
            self, "Database Taken Over",
            f"Another FinanceBook took over this database.\n\n{who}.\n\n"
            "This window is now read-only so the two don't overwrite each other. "
            "Everything you saved before now is kept.")

    def release_database(self):
        """Close the current database cleanly and give up its lock."""
        self._heartbeat.stop()
        try:
            self.db.close()
        finally:
            if self._lock is not None:
                self._lock.release()
            self._lock = None

    def closeEvent(self, event):
        self.release_database()
        super().closeEvent(event)

    def _build_nav(self):
        """(Re)build the grouped page list for the current database's book type
        and, for a Business book, its enabled modules."""
        self._modules = NC.enabled_modules(self.db)
        business = self.db.is_business()
        holder = QWidget()
        holder.setAutoFillBackground(False)
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._nav_buttons = {}
        for header, items in NC.visible_sections(self.db.book_type(), self._modules):
            if not items:
                continue
            lbl = QLabel(header)
            lbl.setObjectName("SidebarGroup")
            lay.addWidget(lbl)
            for item in items:
                # "&&": a lone & would be read as a keyboard mnemonic and vanish.
                btn = NavButton(item.label.replace("&", "&&"), item.icon, holder)
                btn.setMinimumHeight(36)
                btn.clicked.connect(lambda checked, n=item.id: self._nav_to(n))
                lay.addWidget(btn)
                self._nav_buttons[item.id] = btn
        self._add_features_btn = None
        if business:
            lay.addSpacing(4)
            add = QPushButton("＋  Add features…")
            add.setObjectName("SidebarLink")
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.setToolTip("Turn business features on or off (Settings → Sidebar & Features)")
            add.clicked.connect(self.open_feature_settings)
            lay.addWidget(add)
            self._add_features_btn = add
        lay.addStretch()
        old = self._nav_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._nav_scroll.setWidget(holder)
        self._nav_buttons["Settings"] = self._settings_btn
        self._book_badge.setText("BUSINESS BOOK" if business else "PERSONAL BOOK")
        self._mark_active()

    def _mark_active(self):
        hit = self._locate(self._current_page) if self._current_page else None
        active = (hit[0].id if hit and hit[0] is not None else self._current_page)
        for n, b in self._nav_buttons.items():
            b.set_active(n == active)

    def refresh_navigation(self):
        """Call after the book type or the enabled modules change: rebuild the
        sidebar and drop cached pages so they pick up the differences. The page
        on screen is kept (moved into its new hub, if it has one)."""
        current = self._current_page
        self._build_nav()
        self._drop_pages(keep=current, keep_hub=False)
        if current and self._locate(current) is not None:
            self._nav_to(current)
        else:
            self._drop_pages()
            self._nav_to("Dashboard")

    def set_modules(self, enabled):
        """Save which optional Business modules show, then rebuild the sidebar."""
        NC.save_modules(self.db, enabled)
        self.refresh_navigation()

    def offer_module(self, key: str) -> bool:
        """Something asked for a page whose module is off: offer to turn it on.
        Returns True if it is now on."""
        m = NC.MODULES_BY_KEY[key]
        if self.db.read_only:
            QMessageBox.information(
                self, f"{m.label} Is Turned Off",
                f"{m.label} is turned off for this book, and the database is open read-only, "
                "so it can't be turned on from here.")
            return False
        if QMessageBox.question(
                self, f"Turn On {m.label}?",
                f"{m.label} is turned off for this book.\n\nTurn on {m.label}? It's added "
                "back to the sidebar. Its data was never removed. You can turn it off again "
                "in Settings → Sidebar & Features.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return False
        self.set_modules(self._modules | {key})
        return True

    def reveal_add_features(self):
        """The '＋ Add features…' link, scrolled into view (for the tour)."""
        if self._add_features_btn is not None:
            self._nav_scroll.ensureWidgetVisible(self._add_features_btn)
        return self._add_features_btn

    def open_feature_settings(self):
        self._nav_to("Settings")
        page = self._page_cache.get("Settings")
        if page is not None and hasattr(page, "show_features_section"):
            page.show_features_section()

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
        self._db_combo.addItem("🔗  Connect Existing Database…", "__connect__")
        self._db_combo.setCurrentIndex(current_idx)
        self._db_combo.blockSignals(False)

    def _on_db_selected(self, index: int):
        path = self._db_combo.itemData(index)
        if path == "__new__":
            self._create_new_database()
            return
        if path == "__connect__":
            self.connect_existing_database()
            return
        if _same_path(path, self.db.db_path):
            return  # already on this one
        if not self.open_database(path):
            self._populate_db_combo()

    def connect_existing_database(self) -> bool:
        """Pick an existing database file (e.g. in OneDrive) and switch to it."""
        from ui.connect_dialog import choose_existing_database
        path = choose_existing_database(self)
        if not path:
            self._populate_db_combo()
            return False
        if _same_path(path, self.db.db_path):
            self._populate_db_combo()
            QMessageBox.information(self, "Connect Database", "That database is already open.")
            return True
        if not self.open_database(path):
            self._populate_db_combo()
            return False
        return True

    def open_database(self, path: str, book_type: str | None = None) -> bool:
        """
        Make path the active database: take its lock (asking if another
        computer has it), open it, then let go of the current one. Returns
        False if the user cancelled or it couldn't be opened — the current
        database stays open and untouched.
        """
        from ui.connect_dialog import open_with_lock
        reopening = _same_path(path, self.db.db_path)
        if reopening and self._lock is not None:
            return True     # "Open for Editing" while we already hold the lock
        opened = open_with_lock(self, path, book_type=book_type)
        if opened is None:
            return False
        new_db, new_lock = opened
        if not reopening:
            self.release_database()
        self._switch_to(new_db, new_lock)
        return True

    def reopen_current(self):
        """Re-open the current file under the lock we already hold (after a restore)."""
        from sheets.client import Database
        self._switch_to(Database(self.db.db_path, read_only=self.db.read_only), self._lock)

    def _create_new_database(self):
        from ui.database_dialog import DatabaseSetupDialog
        dialog = DatabaseSetupDialog(
            self,
            window_title="New Database",
            heading="Create a New Database",
            message="Give your new database a name and choose where to save it.",
            default_name="My Finances",
            default_location=self._base_dir,
            accept_label="Create",
            show_book_type=True)
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
        if not self.open_database(path, book_type=dialog.book_type):
            self._populate_db_combo()
            return
        self.db.set_setting("database_name", name)
        if dialog.book_type == "business":
            self.set_modules(NC.preset_modules(dialog.preset))
        self._populate_db_combo()

    def delete_current_database(self) -> bool:
        """
        Delete the open database: switch to another one, take this one off the
        list, and move its file to the Recycle Bin (or delete it, if there is no
        Recycle Bin and the user agrees). Returns True if it was removed.
        """
        from PyQt6.QtWidgets import QInputDialog
        from PyQt6.QtCore import QFile
        from db_config import get_known_databases, forget_database
        import db_lock
        path = os.path.abspath(self.db.db_path)
        name = self.db.get_setting("database_name") or os.path.basename(path)
        if self.db.read_only:
            QMessageBox.information(
                self, "Delete Database",
                f"“{name}” is open read-only because it's in use elsewhere, so it can't be "
                "deleted from here.")
            return False
        others = [p for p in get_known_databases() if not _same_path(p, path)]
        if not others:
            QMessageBox.information(
                self, "Delete Database",
                f"“{name}” is your only database. Create another one from the database "
                "dropdown first, then delete this one.")
            return False
        typed, ok = QInputDialog.getText(
            self, "Delete Database",
            f"This removes “{name}” from FinanceBook and moves its file to the Recycle Bin:\n"
            f"{path}\n\nBackups in the “FinanceBook Backups” folder and receipt files are "
            f"kept.\n\nType the database name to confirm:")
        if not ok:
            return False
        if typed.strip() != name:
            QMessageBox.information(self, "Delete Database",
                                    "The name didn't match, so nothing was deleted.")
            return False
        target = others[0]
        if not self.open_database(target):
            return False        # couldn't switch away (cancelled / in use) — nothing deleted
        forget_database(path)
        self._populate_db_combo()
        # We released our lock when switching away; a lock here now means
        # another computer opened it in the meantime.
        info = db_lock.read_lock(path)
        if info is not None and not db_lock.is_stale(info):
            QMessageBox.warning(
                self, "Delete Database",
                f"“{name}” was just opened somewhere else ({db_lock.describe(info)}), so "
                "its file was left in place. It's been removed from this computer's list only.")
            return False
        leftovers = []
        for f in (path, path + "-wal", path + "-shm", path + "-journal",
                  db_lock.lock_path(path)):
            if os.path.exists(f):
                ok_trash, _ = QFile.moveToTrash(f)
                if not ok_trash:
                    leftovers.append(f)
        if leftovers:
            if QMessageBox.question(
                    self, "No Recycle Bin",
                    "This location has no Recycle Bin (for example a network drive). Delete the "
                    "database file permanently?\n\nIf you choose No, it's only removed from "
                    "FinanceBook's list and the file stays where it is.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                for f in leftovers:
                    try:
                        os.remove(f)
                    except OSError as e:
                        QMessageBox.warning(self, "Delete Database",
                                            f"Couldn't delete {os.path.basename(f)}: {e.strerror}")
                        break
        QMessageBox.information(self, "Database Deleted",
                                f"“{name}” was deleted. You're now in "
                                f"“{_read_db_name(target)}”.")
        return True

    def _switch_to(self, new_db, lock=None):
        """Show new_db — already opened and, unless read-only, locked by `lock`.
        The caller releases the previous database first (see open_database)."""
        self.db = new_db
        self._lock = lock
        from db_config import register_database
        register_database(os.path.abspath(new_db.db_path), make_active=True)
        self._drop_pages()
        # These three do nothing on a read-only database.
        new_db.auto_backup_if_due()
        from db_config import current_user_name
        new_db.set_session_user(current_user_name())
        try:
            from datetime import date as _date
            new_db.process_recurring(_date.today().isoformat())
        except Exception:
            pass
        self._populate_db_combo()
        self._current_page = ""
        self._build_nav()
        self._status_label.setText(f"● {os.path.basename(new_db.db_path)}")
        self._update_lock_state()
        self._nav_to("Dashboard")

    # ── navigation ────────────────────────────────────────────────────────────

    def _load_page(self, name: str) -> QWidget:
        if name in self._page_cache:
            return self._page_cache[name]
        cls  = _page_constructors()[name]
        page = cls(self.db)
        # Let pages request cross-page navigation (e.g. click an account name to
        # jump to its transactions).
        page._main_window = self
        _clear_scroll_backgrounds(page)
        self._page_cache[name] = page
        return page

    def _hub(self, item) -> HubPage:
        hub = self._hub_cache.get(item.id)
        if hub is None:
            hub = HubPage(item, self._nav_to)
            self._stack.addWidget(hub)
            self._hub_cache[item.id] = hub
        return hub

    def _discard(self, w: QWidget):
        self._stack.removeWidget(w)
        for hub in self._hub_cache.values():
            if hub.holds(w):
                hub.release(w)
        w.deleteLater()

    def _drop_pages(self, keep: str = "", keep_hub: bool = True):
        """Delete cached pages (and hubs) except `keep`. With keep_hub=False the
        kept page is lifted out of its hub so a rebuilt hub can adopt it."""
        kept = self._page_cache.get(keep)
        kept_hub = None
        for hub in self._hub_cache.values():
            if kept is not None and hub.holds(kept):
                if keep_hub:
                    kept_hub = hub
                else:
                    hub.release(kept)
        for name in list(self._page_cache):
            if name != keep:
                self._discard(self._page_cache.pop(name))
        for hid in list(self._hub_cache):
            if self._hub_cache[hid] is not kept_hub:
                hub = self._hub_cache.pop(hid)
                self._stack.removeWidget(hub)
                hub.deleteLater()

    def _locate(self, name: str):
        return NC.locate(name, self.db.book_type(), self._modules)

    def _page_index(self, name: str):
        """Key for a page (or sidebar item) by name, 'Settings' included, or None
        if it isn't shown in this book. Pass it to _nav_to()."""
        return name if self._locate(name) is not None else None

    def page(self, name: str):
        """Navigate to a page by name and return it (None if unavailable). Pages
        inside a hub are returned themselves, not the hub."""
        hit = self._locate(name)
        if hit is None:
            return None
        self._nav_to(name)
        return self._page_cache.get(hit[1])

    def current_page_widget(self):
        """The page on screen (inside its hub, if it has one)."""
        return self._page_cache.get(self._current_page)

    def start_tour(self):
        """Launch the interactive guided tour (coach marks over real buttons)."""
        from ui.guided_tour import GuidedTour
        self._tour = GuidedTour(self)
        self._tour.start()

    def open_account_transactions(self, account_id: str):
        """Jump to the Transactions page filtered to one account (recent first)."""
        page = self.page("Transactions")
        if page and hasattr(page, "show_account"):
            page.show_account(account_id)

    def _nav_to(self, name: str):
        hit = self._locate(name)
        if hit is None:
            module = NC.module_for_page(name)
            if module and self.db.is_business():
                # A link to a module that's turned off: ask rather than fail silently.
                if not self.offer_module(module):
                    return
                hit = self._locate(name)
            elif name in _page_constructors():
                hit = (None, name)      # e.g. a Business page asked for in a Personal book
            else:
                hit = self._locate("Dashboard")
            if hit is None:
                return
        item, name = hit
        self._current_page = name
        self._mark_active()
        page = self._load_page(name)
        if item is not None and item.is_hub:
            self._stack.removeWidget(page)
            hub = self._hub(item)
            hub.show_page(name, page)
            self._stack.setCurrentWidget(hub)
        else:
            for hub in self._hub_cache.values():
                if hub.holds(page):
                    hub.release(page)
            if self._stack.indexOf(page) < 0:
                self._stack.addWidget(page)
            self._stack.setCurrentWidget(page)
        if hasattr(page, "refresh"):
            page.refresh()

    def _reload(self):
        try:
            self.db.reload()
            current = self.current_page_widget()
            if hasattr(current, "refresh"):
                current.refresh()
            self._drop_pages(keep=self._current_page)
            self._status_label.setText("● Refreshed")
            # A timer owned by the window: a free-standing singleShot would still fire
            # (and touch a deleted label) if the window closed within 3 seconds.
            self._status_timer.start(3000)
        except Exception as e:
            QMessageBox.warning(self, "Reload Failed", str(e))
