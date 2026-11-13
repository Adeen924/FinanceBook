"""
The "Accounts" page — a QuickBooks-style Chart of Accounts.

One list grouped by type (Bank, Credit Card, … Equity, Income, Other Income,
Expense, Other Expense, Debt Repayment, Asset / Inventory Purchase). Balance-
sheet accounts live in the `accounts` table and P&L categories in `categories`
(see utils/chart_of_accounts.py); this page only unifies how they are shown and
edited. Tabs: Chart of Accounts, Inactive Accounts, Classes.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QMessageBox, QHeaderView, QLabel, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QDialog, QFormLayout,
                             QComboBox, QDialogButtonBox, QCheckBox)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont
from ui.widgets import PageTitle, DataTable, SecondaryButton
from ui.styles import SUCCESS, DANGER, ACCENT, MUTED
from utils import chart_of_accounts as COA

ROW_H = 44  # px — tall enough for buttons + padding

COL_NAME, COL_TYPE, COL_DETAIL, COL_BALANCE, COL_ACTIONS = range(5)


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


class AccountsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._anims = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Accounts"))
        hdr.addStretch()
        self._sub_btn = SecondaryButton("+ Sub-account")
        self._sub_btn.setToolTip("Add an income / expense account under another one")
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

    def _build_chart_tab(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        pl.setSpacing(10)
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
        pl.addWidget(self._tree, 1)
        return page

    def _make_inactive_tab(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        pl.setSpacing(10)
        table = DataTable(["Name", "Type", "Institution", "Currency", "Balance", "Actions"])
        table.setColumnWidth(0, 220)
        table.setColumnWidth(1, 120)
        table.setColumnWidth(2, 200)
        table.setColumnWidth(3, 90)
        table.setColumnWidth(4, 150)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(5, 190)
        table.verticalHeader().setDefaultSectionSize(ROW_H)
        pl.addWidget(table)
        total = QLabel("")
        total.setObjectName("Muted")
        pl.addWidget(total)
        self._tabs.addTab(page, "Inactive Accounts")
        return table, total

    # ── loading ────────────────────────────────────────────────────────────────

    def refresh(self):
        active = self.db.get_accounts(active=1)
        inactive = self.db.get_accounts(active=0)
        self._fill_chart(active)
        self._fill_inactive(inactive)
        self._classes.refresh()
        self._tabs.setTabText(1, f"Inactive Accounts ({len(inactive)})")

    def _toggle_personal(self, on: bool):
        self.db.set_setting("show_personal_categories", "1" if on else "0")
        self.refresh()

    def _fill_chart(self, accounts):
        self._tree.clear()
        cats = self.db.get_categories_for_book()
        business = self.db.is_business()
        from ui.pages.categories import type_label
        net = 0.0
        self._first_account_item = None
        for group, entries in COA.chart_rows(accounts, cats):
            head = QTreeWidgetItem([group.label.upper(), "", "", "", ""])
            f = QFont(head.font(0)); f.setBold(True)
            head.setFont(COL_NAME, f)
            head.setForeground(COL_NAME, QColor(MUTED))
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._tree.addTopLevelItem(head)
            group_total = 0.0
            for e in entries:
                d = e["data"]
                if e["kind"] == "account":
                    bal = self.db.account_balance(d["id"])
                    group_total += bal
                    item = QTreeWidgetItem(["    " + d.get("name", ""),
                                            (d.get("type") or "").title(),
                                            d.get("institution", "") or "—",
                                            _money(bal), ""])
                    item.setForeground(COL_NAME, QColor(ACCENT))
                    fn = QFont(item.font(COL_NAME)); fn.setUnderline(True)
                    item.setFont(COL_NAME, fn)
                    item.setToolTip(COL_NAME, "Click to view this account's transactions")
                    item.setForeground(COL_BALANCE, QColor(SUCCESS if bal >= 0 else DANGER))
                    if self._first_account_item is None:
                        self._first_account_item = item
                else:
                    indent = "        ↳ " if e["depth"] else "    "
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
                item.setSizeHint(COL_NAME, QSize(0, ROW_H))
                self._tree.addTopLevelItem(item)
                self._tree.setItemWidget(item, COL_ACTIONS, self._action_cell(e))
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

    def _fill_inactive(self, accounts):
        table = self._inactive_table
        table.clear_rows()
        table.setRowCount(len(accounts))
        for row, acct in enumerate(accounts):
            bal = self.db.account_balance(acct["id"])
            table.set_item(row, 0, acct.get("name", ""), bold=True)
            table.set_item(row, 1, (acct.get("type") or "").title())
            table.set_item(row, 2, acct.get("institution", "") or "—")
            table.set_item(row, 3, acct.get("currency", "USD"),
                           align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            table.money_item(row, 4, bal)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 5, 6, 5)
            cl.setSpacing(6)
            edit_btn = QPushButton("Edit")
            edit_btn.setObjectName("CellButton")
            edit_btn.clicked.connect(lambda _, a=acct: self._edit_account(a))
            cl.addWidget(edit_btn)
            react_btn = QPushButton("Reactivate")
            react_btn.setObjectName("CellSuccess")
            react_btn.clicked.connect(lambda _, a=acct: self._reactivate(a))
            cl.addWidget(react_btn)
            table.setCellWidget(row, 5, cell)
            table.setRowHeight(row, ROW_H)
        n = len(accounts)
        self._inactive_total.setText(f"{n} inactive account{'s' if n != 1 else ''}")

    def _action_cell(self, entry) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("CellButton")
        edit_btn.clicked.connect(lambda _, e=entry: self._edit_entry(e))
        lay.addWidget(edit_btn)
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

    def _delete_entry(self, e):
        if e["kind"] == "account":
            self._delete_account(e["data"])
        else:
            self._delete_category(e["data"])

    def _add(self):
        dlg = NewAccountTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        kind, atype = dlg.choice()
        if kind == "account":
            from ui.dialogs.account_dialog import AccountDialog
            ad = AccountDialog(institutions=self.db.get_institutions(), parent=self,
                               account_type=atype)
            if ad.exec() == QDialog.DialogCode.Accepted:
                self.db.save_account(ad.get_data())
                self.refresh()
        else:
            from ui.pages.categories import CategoryDialog
            cd = CategoryDialog(self.db, parent=self, top_level=True, category_type=atype)
            if cd.exec() == QDialog.DialogCode.Accepted:
                saved = self.db.save_category(cd.get_data())
                self.refresh()
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
            self.refresh()
            self._flash(saved.get("id"))

    def _edit_account(self, acct):
        from ui.dialogs.account_dialog import AccountDialog
        balance = self.db.account_balance(acct["id"])
        dlg = AccountDialog(account=acct, institutions=self.db.get_institutions(),
                            balance=balance, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_account(dlg.get_data())
            self.refresh()

    def _edit_category(self, cat):
        from ui.pages.categories import CategoryDialog
        dlg = CategoryDialog(self.db, edit_cat=cat, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_category(dlg.get_data())
            self.refresh()

    def _reactivate(self, acct):
        self.db.set_account_active(acct["id"], True)
        self.refresh()

    def _delete_account(self, acct):
        reply = QMessageBox.question(
            self, "Delete Account",
            f"Delete '{acct['name']}'?\n\nTransactions will remain in the system.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_account(acct["id"])
            self.refresh()

    def _delete_category(self, cat):
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete '{cat['name']}'? Transactions assigned to it will become "
            "uncategorized.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_category(cat["id"])
            self.refresh()

    def _flash(self, cat_id):
        from ui.pages.categories import flash_tree_item
        target = None
        for i in range(self._tree.topLevelItemCount()):
            it = self._tree.topLevelItem(i)
            e = self._entry(it)
            if e and e["kind"] == "category" and e["data"].get("id") == cat_id:
                target = it
                break
        flash_tree_item(self, self._tree, target, self._anims)


def _money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0 else f"(${abs(v):,.2f})"
