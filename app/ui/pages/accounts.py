"""
The "Accounts" page — a QuickBooks-style Chart of Accounts.

One list grouped by type (Bank, Credit Card, … Equity, Income, Other Income,
Cost of Goods Sold, Expense, Other Expense, Debt Repayment, Asset / Inventory
Purchase). Balance-sheet accounts live in the `accounts` table and P&L
categories in `categories` (see utils/chart_of_accounts.py); this page only
unifies how they are shown and edited. Tabs: Chart of Accounts, Inactive
Accounts (balance-sheet accounts and income / expense accounts), Classes.

Since v1.18 an account in use can't be deleted — it can be made inactive —
the page stays on the row that was edited, and a search box filters the chart.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QMessageBox, QHeaderView, QLabel, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QDialog, QFormLayout,
                             QComboBox, QDialogButtonBox, QCheckBox, QMenu,
                             QInputDialog, QLineEdit, QAbstractItemView)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QShortcut, QKeySequence, QBrush
from ui.widgets import PageTitle, DataTable, SecondaryButton
from ui.styles import SUCCESS, DANGER, ACCENT, MUTED
from utils import chart_of_accounts as COA

ROW_H = 44  # px — tall enough for buttons + padding

COL_NAME, COL_TYPE, COL_DETAIL, COL_BALANCE, COL_ACTIONS = range(5)

NO_MATCH_TEXT = "No accounts match"


class NewAccountTypeDialog(QDialog):
    """First step of "+ New Account": what kind of account is it?"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Account")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        for label, value in COA.NEW_ACCOUNT_CHOICES:
            self.type_combo.addItem(label, value)
        form.addRow("Account type *", self.type_combo)
        lay.addLayout(form)
        self._hint = QLabel("")
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        self.type_combo.currentIndexChanged.connect(self._sync_hint)
        self._sync_hint()
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _sync_hint(self, *_):
        kind, _t = COA.resolve_choice(self.type_combo.currentData())
        self._hint.setText(
            "A balance-sheet account: it has a balance and its own register."
            if kind == "account" else
            "An income / expense account (category): transactions are filed under it "
            "on the Profit & Loss.")

    def choice(self) -> tuple:
        return COA.resolve_choice(self.type_combo.currentData())


def _key(e) -> tuple:
    """(kind, id) — what identifies a row across a rebuild."""
    return (e["kind"], e["data"].get("id", "")) if e else None


class AccountsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._anims = []
        self._focus = None              # (kind, id) to land on after the next refresh
        self._items: dict = {}          # (kind, id) → chart item
        self._orig_fg: dict = {}        # id(item) → [QBrush per column] (search dimming)
        self._inactive_rows: list = []  # entries on the Inactive tab, in row order
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Accounts"))
        hdr.addStretch()
        self._sub_btn = SecondaryButton("+ Sub-account")
        self._sub_btn.setToolTip("Add an income / expense account under another one. "
                                 "To nest a bank, card or other balance-sheet account, "
                                 "use Move… or its Edit dialog.")
        self._sub_btn.clicked.connect(self._add_sub)
        hdr.addWidget(self._sub_btn)
        self._add_btn = QPushButton("+ New Account")
        self._add_btn.clicked.connect(self._add)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        info = QLabel("Categories are now part of Accounts: bank and card accounts, loans "
                      "and equity, and every income and expense account, in one list. "
                      "Click a bank or card name to open its register; double-click any "
                      "row to edit it.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_chart_tab(), "Chart of Accounts")
        self._inactive_table, self._inactive_total = self._make_inactive_tab()
        from ui.pages.categories import ClassesPanel
        self._classes = ClassesPanel(self.db)
        self._tabs.addTab(self._classes, "Classes")
        lay.addWidget(self._tabs)

        self._find_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        self._find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self.focus_search)

    def _build_chart_tab(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        pl.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search accounts — name, type, institution or "
                                        "Schedule C line  (Ctrl+F)")
        self._search.setToolTip("Filters the Chart of Accounts as you type")
        self._search.textChanged.connect(self._apply_search)
        search_row.addWidget(self._search, 1)
        self._search_clear = QPushButton("✕")
        self._search_clear.setObjectName("Secondary")
        self._search_clear.setToolTip("Clear the search")
        self._search_clear.setFixedWidth(40)
        self._search_clear.clicked.connect(self._search.clear)
        self._search_clear.setVisible(False)
        search_row.addWidget(self._search_clear)
        pl.addLayout(search_row)

        bar = QHBoxLayout()
        self._show_personal = None
        if self.db.is_business():
            # Personal categories (from before the book became a business
            # book) are kept but hidden from pickers unless this is on.
            self._show_personal = QCheckBox("Show personal categories")
            self._show_personal.setToolTip(
                "Personal categories stay in your books. Used ones are always shown; "
                "tick this to offer the rest in every account list too.")
            self._show_personal.setChecked(
                self.db.get_setting("show_personal_categories") == "1")
            self._show_personal.toggled.connect(self._toggle_personal)
            bar.addWidget(self._show_personal)
        bar.addStretch()
        self._total_lbl = QLabel("")
        self._total_lbl.setObjectName("Muted")
        bar.addWidget(self._total_lbl)
        pl.addLayout(bar)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Type",
                                    "Schedule C / Institution" if self.db.is_business()
                                    else "Institution", "Balance", ""])
        self._tree.setColumnWidth(COL_NAME, 320)
        self._tree.setColumnWidth(COL_TYPE, 170)
        self._tree.setColumnWidth(COL_DETAIL, 220)
        self._tree.setColumnWidth(COL_BALANCE, 130)
        self._tree.header().setSectionResizeMode(COL_ACTIONS, QHeaderView.ResizeMode.Stretch)
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(lambda it, _c: self._edit_item(it))
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        pl.addWidget(self._tree, 1)

        self._no_match = QLabel(NO_MATCH_TEXT)
        self._no_match.setObjectName("Muted")
        self._no_match.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_match.setVisible(False)
        pl.addWidget(self._no_match)
        return page

    def _make_inactive_tab(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        pl.setSpacing(10)
        note = QLabel("Inactive accounts are hidden from account lists and the Chart of "
                      "Accounts. Their transactions keep them, by name, in the register "
                      "and reports. Reactivate one to use it again.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        pl.addWidget(note)
        table = DataTable(["Name", "Type", "Institution / Schedule C", "Currency", "Balance",
                           "Actions"])
        table.setColumnWidth(0, 220)
        table.setColumnWidth(1, 140)
        table.setColumnWidth(2, 200)
        table.setColumnWidth(3, 90)
        table.setColumnWidth(4, 150)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(5, 190)
        table.verticalHeader().setDefaultSectionSize(ROW_H)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._inactive_menu)
        table.cellDoubleClicked.connect(
            lambda r, _c: self._edit_entry(self._inactive_rows[r])
            if 0 <= r < len(self._inactive_rows) else None)
        pl.addWidget(table)
        total = QLabel("")
        total.setObjectName("Muted")
        pl.addWidget(total)
        self._tabs.addTab(page, "Inactive Accounts")
        return table, total

    # ── loading ────────────────────────────────────────────────────────────────

    def refresh(self, focus=None):
        """Rebuild every tab. The page stays where it was: the entry in `focus`
        ((kind, id) — the one just edited / added / moved), else the selected
        one, is re-selected and scrolled to the centre; when it's no longer in
        the list the old scroll position is kept. The search filter stays."""
        focus = focus or self._focus or _key(self._entry(self._tree.currentItem()))
        self._focus = None
        chart_scroll = self._tree.verticalScrollBar().value()
        ina = self._inactive_table
        ina_focus = focus if focus else (
            _key(self._inactive_rows[ina.currentRow()])
            if 0 <= ina.currentRow() < len(self._inactive_rows) else None)
        ina_scroll = ina.verticalScrollBar().value()

        active = self.db.get_accounts(active=1)
        inactive = self.db.get_accounts(active=0)
        inactive_cats = self.db.get_inactive_categories()
        self._fill_chart(active)
        self._fill_inactive(inactive, inactive_cats)
        self._classes.refresh()
        n = len(inactive) + len(inactive_cats)
        self._tabs.setTabText(1, f"Inactive Accounts ({n})")
        self._apply_search()
        self._restore(focus, chart_scroll)
        self._restore_inactive(ina_focus, ina_scroll)

    def _restore(self, focus, scroll: int):
        item = self._items.get(focus) if focus else None
        if item is not None and not item.isHidden():
            self._tree.setCurrentItem(item)
            # The tree is laid out lazily; scroll once now and again when the
            # event loop has sized it (a page that isn't shown yet).
            self._tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            QTimer.singleShot(0, lambda it=item: self._scroll_to(it))
        else:
            self._tree.setCurrentItem(None)
            self._tree.verticalScrollBar().setValue(scroll)

    def _scroll_to(self, item):
        try:
            self._tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        except RuntimeError:
            pass                                # rebuilt in the meantime

    def _restore_inactive(self, focus, scroll: int):
        t = self._inactive_table
        row = next((i for i, e in enumerate(self._inactive_rows) if _key(e) == focus), -1) \
            if focus else -1
        if row >= 0:
            t.selectRow(row)
            t.scrollToItem(t.item(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            t.clearSelection()
            t.verticalScrollBar().setValue(scroll)

    def _toggle_personal(self, on: bool):
        self.db.set_setting("show_personal_categories", "1" if on else "0")
        self.refresh()

    def _fill_chart(self, accounts):
        self._tree.clear()
        self._items = {}
        self._orig_fg = {}
        cats = self.db.get_categories_for_book()
        business = self.db.is_business()
        from ui.pages.categories import type_label
        net = 0.0
        self._first_account_item = None
        rows = COA.chart_rows(accounts, cats)
        # A parent account shows its balance with its sub-accounts rolled up;
        # group totals and the net add each account once (its own balance).
        own = {e["data"]["id"]: self.db.account_balance(e["data"]["id"])
               for _g, es in rows for e in es if e["kind"] == "account"}
        rolled = dict(own)
        for _g, es in rows:
            for e in reversed(es):
                if e["kind"] == "account" and e.get("parent"):
                    rolled[e["parent"]] = rolled.get(e["parent"], 0.0) + rolled[e["data"]["id"]]
        for group, entries in rows:
            head = QTreeWidgetItem([group.label.upper(), "", "", "", ""])
            f = QFont(head.font(0)); f.setBold(True)
            head.setFont(COL_NAME, f)
            head.setForeground(COL_NAME, QColor(MUTED))
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            head.setData(COL_NAME, Qt.ItemDataRole.UserRole + 1, group.key)
            self._tree.addTopLevelItem(head)
            group_total = 0.0
            for e in entries:
                d = e["data"]
                indent = "    " + "    " * e["depth"] + ("↳ " if e["depth"] else "")
                if e["kind"] == "account":
                    group_total += own[d["id"]]
                    bal = rolled[d["id"]]
                    item = QTreeWidgetItem([indent + d.get("name", ""),
                                            (d.get("type") or "").title(),
                                            d.get("institution", "") or "—",
                                            _money(bal), ""])
                    item.setForeground(COL_NAME, QColor(ACCENT))
                    fn = QFont(item.font(COL_NAME)); fn.setUnderline(True)
                    item.setFont(COL_NAME, fn)
                    item.setToolTip(COL_NAME, "Click to view this account's transactions")
                    item.setForeground(COL_BALANCE, QColor(SUCCESS if bal >= 0 else DANGER))
                    if abs(bal - own[d["id"]]) >= 0.005:
                        item.setToolTip(COL_BALANCE, "Includes its sub-accounts. This account "
                                        f"alone: {_money(own[d['id']])}")
                    if self._first_account_item is None:
                        self._first_account_item = item
                else:
                    detail = ""
                    if business:
                        line = d.get("tax_line") or ""
                        pct = float(d.get("deduct_pct") or 100)
                        detail = ((f"Line {line}" if line else "—")
                                  + (f"  ({pct:.0f}% deductible)" if pct < 100 else ""))
                    item = QTreeWidgetItem([indent + d.get("name", ""),
                                            type_label(d.get("type", "")), detail, "", ""])
                item.setTextAlignment(COL_BALANCE, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
                item.setData(COL_NAME, Qt.ItemDataRole.UserRole, e)
                item.setData(COL_NAME, Qt.ItemDataRole.UserRole + 1, group.key)
                item.setSizeHint(COL_NAME, QSize(0, ROW_H))
                self._tree.addTopLevelItem(item)
                self._tree.setItemWidget(item, COL_ACTIONS, self._action_cell(e))
                self._items[_key(e)] = item
                self._orig_fg[id(item)] = [QBrush(item.foreground(c)) for c in range(5)]
            if group.kind == "account":
                net += group_total
                head.setText(COL_BALANCE, _money(group_total))
                head.setFont(COL_BALANCE, f)
                head.setTextAlignment(COL_BALANCE, Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        sign = "$" if net >= 0 else "-$"
        self._total_lbl.setText(f"{len(accounts)} active balance-sheet account"
                                f"{'s' if len(accounts) != 1 else ''}  —  "
                                f"Net balance: {sign}{abs(net):,.2f}")

    def _fill_inactive(self, accounts, categories=()):
        from ui.pages.categories import type_label
        table = self._inactive_table
        table.clear_rows()
        entries = ([{"kind": "account", "data": a} for a in accounts]
                   + [{"kind": "category", "data": c} for c in categories])
        self._inactive_rows = entries
        table.setRowCount(len(entries))
        cat_names = {c["id"]: c.get("name", "") for c in
                     self.db.get_categories(include_inactive=True)} if categories else {}
        for row, e in enumerate(entries):
            d = e["data"]
            if e["kind"] == "account":
                bal = self.db.account_balance(d["id"])
                table.set_item(row, 0, d.get("name", ""), bold=True)
                table.set_item(row, 1, (d.get("type") or "").title())
                table.set_item(row, 2, d.get("institution", "") or "—")
                table.set_item(row, 3, d.get("currency", "USD"),
                               align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                table.money_item(row, 4, bal)
            else:
                parent = cat_names.get(d.get("parent_id") or "", "")
                name = f"{parent} → {d.get('name', '')}" if parent else d.get("name", "")
                table.set_item(row, 0, name, bold=True)
                table.set_item(row, 1, type_label(d.get("type", "")))
                line = d.get("tax_line") or ""
                table.set_item(row, 2, f"Line {line}" if line else "—")
                table.set_item(row, 3, "—",
                               align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                table.set_item(row, 4, "—",
                               align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 5, 6, 5)
            cl.setSpacing(6)
            edit_btn = QPushButton("Edit")
            edit_btn.setObjectName("CellButton")
            edit_btn.clicked.connect(lambda _, x=e: self._edit_entry(x))
            cl.addWidget(edit_btn)
            react_btn = QPushButton("Reactivate")
            react_btn.setObjectName("CellSuccess")
            react_btn.clicked.connect(lambda _, x=e: self._reactivate_entry(x))
            cl.addWidget(react_btn)
            table.setCellWidget(row, 5, cell)
            table.setRowHeight(row, ROW_H)
        n = len(entries)
        self._inactive_total.setText(f"{n} inactive account{'s' if n != 1 else ''}")

    def inactive_entries(self) -> list:
        """The entries listed on the Inactive Accounts tab ({"kind", "data"})."""
        return list(self._inactive_rows)

    def _action_cell(self, entry) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("CellButton")
        edit_btn.clicked.connect(lambda _, e=entry: self._edit_entry(e))
        lay.addWidget(edit_btn)
        move_btn = QPushButton("Move…")
        move_btn.setObjectName("CellButton")
        move_btn.setToolTip("Make this a sub-account of another account, or top-level")
        move_btn.setMinimumWidth(move_btn.fontMetrics().horizontalAdvance("Move…") + 34)
        move_btn.clicked.connect(lambda _, e=entry: self._move_dialog(e))
        lay.addWidget(move_btn)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("CellDanger")
        del_btn.clicked.connect(lambda _, e=entry: self._delete_entry(e))
        lay.addWidget(del_btn)
        lay.addStretch()
        return cell

    def first_account_name_rect(self):
        """Global QRect of the first account's Name cell (for the guided tour).
        None if there are no active accounts yet."""
        from PyQt6.QtCore import QRect
        item = getattr(self, "_first_account_item", None)
        if item is None:
            return None
        try:
            r = self._tree.visualItemRect(item)
        except RuntimeError:
            return None
        if r.isNull() or r.width() == 0:
            return None
        r.setWidth(self._tree.columnWidth(COL_NAME))
        return QRect(self._tree.viewport().mapToGlobal(r.topLeft()), r.size())

    # ── search ────────────────────────────────────────────────────────────────

    def focus_search(self):
        """Ctrl+F: show the chart and put the cursor in the search box."""
        self._tabs.setCurrentIndex(0)
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    @staticmethod
    def _search_text(item) -> str:
        return " ".join(item.text(c) for c in (COL_NAME, COL_TYPE, COL_DETAIL)).lower()

    def _apply_search(self, *_):
        """Show only rows matching the search (name, type, institution or
        Schedule C line, case-insensitive). A matching sub-account keeps its
        parents visible, dimmed, and its group header; with nothing matching
        the list says so."""
        q = self._search.text().strip().lower()
        self._search_clear.setVisible(bool(q))
        tree = self._tree
        heads, rows = [], []
        for i in range(tree.topLevelItemCount()):
            it = tree.topLevelItem(i)
            (rows if self._entry(it) else heads).append(it)
        if not q:
            for it in heads + rows:
                it.setHidden(False)
            for it in rows:
                self._set_dim(it, False)
            self._no_match.setVisible(False)
            tree.setVisible(True)
            return
        show: dict = {}                          # id(item) → "match" | "context"
        for it in rows:
            if q in self._search_text(it):
                show[id(it)] = "match"
                e = self._entry(it)
                pid = e.get("parent") or ""
                seen = set()
                while pid and pid not in seen:   # every displayed ancestor
                    seen.add(pid)
                    parent = self._items.get((e["kind"], pid))
                    if parent is None:
                        break
                    show.setdefault(id(parent), "context")
                    pid = self._entry(parent).get("parent") or ""
        groups = set()
        for it in rows:
            state = show.get(id(it))
            it.setHidden(state is None)
            self._set_dim(it, state == "context")
            if state:
                groups.add(it.data(COL_NAME, Qt.ItemDataRole.UserRole + 1))
        for it in heads:
            it.setHidden(it.data(COL_NAME, Qt.ItemDataRole.UserRole + 1) not in groups)
        nothing = not show
        self._no_match.setVisible(nothing)
        tree.setVisible(not nothing)

    def _set_dim(self, item, dim: bool):
        orig = self._orig_fg.get(id(item))
        for c in range(5):
            if dim:
                item.setForeground(c, QColor(MUTED))
            elif orig:
                item.setForeground(c, orig[c])
        item.setData(COL_NAME, Qt.ItemDataRole.UserRole + 2, dim)

    def visible_entries(self) -> list:
        """[(kind, name, dimmed)] of the chart rows on screen (for tests)."""
        out = []
        for i in range(self._tree.topLevelItemCount()):
            it = self._tree.topLevelItem(i)
            e = self._entry(it)
            if e and not it.isHidden():
                out.append((e["kind"], e["data"].get("name", ""),
                            bool(it.data(COL_NAME, Qt.ItemDataRole.UserRole + 2))))
        return out

    # ── actions ────────────────────────────────────────────────────────────────

    def _entry(self, item):
        return item.data(COL_NAME, Qt.ItemDataRole.UserRole) if item else None

    def _on_item_clicked(self, item, col):
        e = self._entry(item)
        if e and e["kind"] == "account" and col == COL_NAME:
            mw = getattr(self, "_main_window", None)
            if mw is not None:
                mw.open_account_transactions(e["data"]["id"])

    def _edit_item(self, item):
        e = self._entry(item)
        if e:
            self._edit_entry(e)

    def _edit_entry(self, e):
        if e["kind"] == "account":
            self._edit_account(e["data"])
        else:
            self._edit_category(e["data"])

    def _ask(self, title: str, text: str, buttons: list) -> str:
        """A warning box with these button labels; returns the one clicked
        (the last one — Cancel — if the box is closed)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if buttons != ["Delete", "Cancel"]
                    else QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        made = {}
        for label in buttons:
            role = (QMessageBox.ButtonRole.RejectRole if label in ("Cancel", "OK")
                    else QMessageBox.ButtonRole.AcceptRole)
            made[box.addButton(label, role)] = label
        cancel = next((b for b, lbl in made.items() if lbl in ("Cancel", "OK")), None)
        if cancel is not None:
            box.setEscapeButton(cancel)
            box.setDefaultButton(cancel)
        box.exec()
        return made.get(box.clickedButton(), buttons[-1])

    # ── move to… ───────────────────────────────────────────────────────────────

    def move_options(self, e) -> list:
        """[(text, parent id)] an entry can move under: "Top level" first, then
        every valid parent — a top-level account other than itself; for a
        balance-sheet account only those in its own group. (Its own
        sub-accounts are never top-level, so no cycle can be offered.)"""
        d = e["data"]
        out = [("Top level", "")]
        if e["kind"] == "account":
            grp = COA.group_for_account(d).key
            for a in sorted(self.db.get_accounts(), key=lambda a: a.get("name", "").lower()):
                if a["id"] != d["id"] and not a.get("parent_id") \
                        and COA.group_for_account(a).key == grp:
                    out.append((a.get("name", ""), a["id"]))
        else:
            from ui.pages.categories import type_label
            for c in sorted(self.db.get_categories_for_book(keep_ids=[d.get("parent_id", "")]),
                            key=lambda c: c.get("name", "").lower()):
                if c["id"] != d["id"] and not c.get("parent_id") \
                        and str(c.get("active", "1")) == "1":
                    out.append((f"{c.get('name', '')}  ({type_label(c.get('type', ''))})",
                                c["id"]))
        return out

    def _move_dialog(self, e):
        opts = self.move_options(e)
        cur = e["data"].get("parent_id") or ""
        texts = [t for t, _v in opts]
        start = next((i for i, (_t, v) in enumerate(opts) if v == cur), 0)
        text, ok = QInputDialog.getItem(
            self, "Move Account", f"Move “{e['data'].get('name', '')}” to:",
            texts, start, False)
        if ok:
            self.move_entry(e, opts[texts.index(text)][1])

    def move_entry(self, e, parent_id: str):
        """Move an account / category under parent_id ("" = top level),
        asking first when its sub-accounts come along or its type changes."""
        d = e["data"]
        if (d.get("parent_id") or "") == (parent_id or ""):
            return
        notes = []
        if parent_id:
            if e["kind"] == "account":
                kids = [a for a in self.db.get_accounts(active=None)
                        if a.get("parent_id") == d["id"]]
                parent = self.db.get_account(parent_id) or {}
            else:
                kids = [c for c in self.db.get_categories(include_inactive=True)
                        if c.get("parent_id") == d["id"]]
                parent = self.db.get_category(parent_id) or {}
                if parent.get("type") and parent.get("type") != d.get("type"):
                    from ui.pages.categories import type_label
                    notes.append(f"It will become {type_label(parent['type'])}, like "
                                 f"“{parent.get('name', '')}” (a sub-account shares its "
                                 "parent's type).")
            if kids:
                n = len(kids)
                notes.insert(0, f"It has {n} sub-account{'s' if n != 1 else ''}, which will "
                                f"move under “{parent.get('name', '')}” too (accounts go one "
                                "level deep).")
        if notes and QMessageBox.question(
                self, "Move Account",
                f"Move “{d.get('name', '')}”?\n\n" + "\n\n".join(notes),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            if e["kind"] == "account":
                self.db.move_account(d["id"], parent_id)
            else:
                self.db.move_category(d["id"], parent_id)
        except ValueError as err:
            QMessageBox.warning(self, "Can't Move", str(err))
            return
        self.refresh(focus=_key(e))

    def _context_menu(self, pos):
        item = self._tree.itemAt(pos)
        e = self._entry(item)
        if not e:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._edit_entry(e))
        move = menu.addMenu("Move to")
        cur = e["data"].get("parent_id") or ""
        for text, value in self.move_options(e):
            act = move.addAction(text, lambda v=value: self.move_entry(e, v))
            act.setCheckable(True)
            act.setChecked(value == cur)
        if cur:
            menu.addAction("Make Top-Level", lambda: self.move_entry(e, ""))
        menu.addSeparator()
        menu.addAction("Make Inactive", lambda: self._make_inactive(e))
        menu.addAction("Delete…", lambda: self._delete_entry(e))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _inactive_menu(self, pos):
        row = self._inactive_table.rowAt(pos.y())
        if not 0 <= row < len(self._inactive_rows):
            return
        e = self._inactive_rows[row]
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self._edit_entry(e))
        menu.addAction("Reactivate", lambda: self._reactivate_entry(e))
        menu.addAction("Delete…", lambda: self._delete_entry(e))
        menu.exec(self._inactive_table.viewport().mapToGlobal(pos))

    # ── inactive / delete ─────────────────────────────────────────────────────

    def _usage(self, e) -> dict:
        ident = e["data"]["id"]
        return (self.db.account_usage(ident) if e["kind"] == "account"
                else self.db.category_usage(ident))

    def _blocked_by_subs(self, e, doing: str) -> bool:
        """True (after saying which) when e has active sub-accounts."""
        d = e["data"]
        subs = self.db.active_sub_accounts(e["kind"], d["id"])
        if not subs:
            return False
        names = ", ".join(f"“{s.get('name', '')}”" for s in subs)
        n = len(subs)
        them = "them" if n != 1 else "it"
        self._ask(f"Can't {doing.title()}",
                  f"“{d.get('name', '')}” has {n} active sub-account{'s' if n != 1 else ''}: "
                  f"{names}.\n\nMove {them} to another account (Move…) or make {them} "
                  f"inactive first, then {doing} “{d.get('name', '')}”.", ["OK"])
        return True

    def _delete_entry(self, e):
        """Delete an unused account (after confirming). One that is used can't
        be deleted: say what uses it and offer Make Inactive instead."""
        d = e["data"]
        name = d.get("name", "")
        if self._blocked_by_subs(e, "delete"):
            return
        usage = self._usage(e)
        if self.db.usage_total(usage):
            n = usage.get("transactions", 0)
            lead = (f"{n} transaction{'s' if n != 1 else ''} use{'s' if n == 1 else ''} "
                    f"“{name}”, so it can't be deleted." if n else
                    f"“{name}” is in use, so it can't be deleted.")
            others = {k: v for k, v in usage.items() if k != "transactions"}
            more = (f" It's also used by {self.db.describe_usage(others)}."
                    if self.db.usage_total(others) else "")
            if str(d.get("active", "1")) == "0":
                self._ask("Can't Delete Account", f"{lead}{more}\n\nIt is already inactive.",
                          ["OK"])
                return
            choice = self._ask(
                "Can't Delete Account",
                f"{lead}{more}\n\nMake it inactive instead? It disappears from account "
                "lists and the Chart of Accounts, but its transactions keep it — by name — "
                "in the register and reports. You can reactivate it any time from the "
                "Inactive Accounts tab.", ["Make Inactive", "Cancel"])
            if choice == "Make Inactive":
                self._make_inactive(e)
            return
        if self._ask("Delete Account",
                     f"Delete “{name}”? Nothing uses it. This can't be undone.",
                     ["Delete", "Cancel"]) != "Delete":
            return
        try:
            if e["kind"] == "account":
                self.db.delete_account(d["id"])
            else:
                self.db.delete_category(d["id"])
        except ValueError as err:
            QMessageBox.warning(self, "Can't Delete", str(err))
        self.refresh()

    def _make_inactive(self, e):
        if self._blocked_by_subs(e, "make inactive"):
            return
        try:
            if e["kind"] == "account":
                self.db.make_account_inactive(e["data"]["id"])
            else:
                self.db.set_category_active(e["data"]["id"], False)
        except ValueError as err:
            QMessageBox.warning(self, "Can't Make Inactive", str(err))
            return
        self.refresh()

    def _reactivate_entry(self, e):
        if e["kind"] == "account":
            self.db.set_account_active(e["data"]["id"], True)
        else:
            self.db.set_category_active(e["data"]["id"], True)
        self.refresh(focus=_key(e))

    def _reactivate(self, acct):
        self._reactivate_entry({"kind": "account", "data": acct})

    def _delete_account(self, acct):
        self._delete_entry({"kind": "account", "data": acct})

    def _delete_category(self, cat):
        self._delete_entry({"kind": "category", "data": cat})

    # ── add / edit ────────────────────────────────────────────────────────────

    def _add(self):
        dlg = NewAccountTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        kind, atype = dlg.choice()
        if kind == "account":
            from ui.dialogs.account_dialog import AccountDialog
            ad = AccountDialog(institutions=self.db.get_institutions(), parent=self,
                               account_type=atype, accounts=self.db.get_accounts())
            if ad.exec() == QDialog.DialogCode.Accepted:
                self._save_account(ad.get_data())
        else:
            from ui.pages.categories import CategoryDialog
            cd = CategoryDialog(self.db, parent=self, top_level=True, category_type=atype)
            if cd.exec() == QDialog.DialogCode.Accepted:
                saved = self.db.save_category(cd.get_data())
                self.refresh(focus=("category", saved.get("id")))
                self._flash(saved.get("id"))

    def _add_sub(self):
        if not [c for c in self.db.get_categories() if not c.get("parent_id")]:
            QMessageBox.information(self, "Create an Account First",
                "A sub-account goes under an income or expense account.\n"
                "Click “+ New Account” first.")
            return
        preset = ""
        e = self._entry(self._tree.currentItem())
        if e and e["kind"] == "category":
            preset = e["data"].get("parent_id") or e["data"]["id"]
        from ui.pages.categories import CategoryDialog
        dlg = CategoryDialog(self.db, parent_id=preset, parent=self, require_parent=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = self.db.save_category(dlg.get_data())
            self.refresh(focus=("category", saved.get("id")))
            self._flash(saved.get("id"))

    def _edit_account(self, acct):
        from ui.dialogs.account_dialog import AccountDialog
        balance = self.db.account_balance(acct["id"])
        dlg = AccountDialog(account=acct, institutions=self.db.get_institutions(),
                            balance=balance, parent=self, accounts=self.db.get_accounts())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._save_account(dlg.get_data())

    def _save_account(self, data):
        try:
            self.db.save_account(data)
        except ValueError as err:
            QMessageBox.warning(self, "Can't Save", str(err))
        self.refresh(focus=("account", data.get("id")) if data.get("id") else None)

    def _edit_category(self, cat):
        from ui.pages.categories import CategoryDialog
        dlg = CategoryDialog(self.db, edit_cat=cat, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.db.save_category(dlg.get_data())
            except ValueError as err:
                QMessageBox.warning(self, "Can't Save", str(err))
            self.refresh(focus=("category", cat.get("id")))

    def _flash(self, cat_id):
        from ui.pages.categories import flash_tree_item
        target = self._items.get(("category", cat_id))
        if target is not None:
            # flash_tree_item clears the selection; keep the new row current.
            flash_tree_item(self, self._tree, target, self._anims)
            self._tree.setCurrentItem(target)
            self._tree.scrollToItem(target, QAbstractItemView.ScrollHint.PositionAtCenter)


def _money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0 else f"(${abs(v):,.2f})"
