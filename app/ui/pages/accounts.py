from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QMessageBox, QHeaderView, QLabel, QTabWidget)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, DataTable
from ui.styles  import SUCCESS, DANGER, MUTED


ROW_H = 44  # px — tall enough for buttons + padding


class AccountsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Accounts"))
        hdr.addStretch()
        add_btn = QPushButton("+ Add Account")
        add_btn.clicked.connect(self._add)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        self._tabs = QTabWidget()
        self._active_table,   self._active_total   = self._make_tab("active")
        self._inactive_table, self._inactive_total = self._make_tab("inactive")
        lay.addWidget(self._tabs)

    def _make_tab(self, kind: str):
        """Build one tab (table + total label). Returns (table, total_label)."""
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        pl.setSpacing(10)

        table = DataTable(
            ["Name", "Type", "Institution", "Currency", "Balance", "Actions"])
        table.setColumnWidth(0, 220)
        table.setColumnWidth(1, 120)
        table.setColumnWidth(2, 200)
        table.setColumnWidth(3, 90)
        table.setColumnWidth(4, 150)
        hv = table.horizontalHeader()
        hv.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(5, 190)
        table.verticalHeader().setDefaultSectionSize(ROW_H)
        pl.addWidget(table)

        total = QLabel("")
        total.setObjectName("Muted")
        pl.addWidget(total)

        self._tabs.addTab(page, "Active Accounts" if kind == "active"
                                else "Inactive Accounts")
        return table, total

    def refresh(self):
        active   = self.db.get_accounts(active=1)
        inactive = self.db.get_accounts(active=0)
        self._fill(self._active_table, self._active_total, active, inactive=False)
        self._fill(self._inactive_table, self._inactive_total, inactive, inactive=True)
        self._tabs.setTabText(0, f"Active Accounts ({len(active)})")
        self._tabs.setTabText(1, f"Inactive Accounts ({len(inactive)})")

    def _fill(self, table, total_label, accounts, inactive: bool):
        table.clear_rows()
        table.setRowCount(len(accounts))

        total = 0.0
        for row, acct in enumerate(accounts):
            bal = self.db.account_balance(acct["id"])
            total += bal

            table.set_item(row, 0, acct.get("name", ""), bold=True)
            table.set_item(row, 1, acct.get("type", "").title())
            table.set_item(row, 2, acct.get("institution", "") or "—")
            table.set_item(row, 3, acct.get("currency", "USD"),
                           align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            table.money_item(row, 4, bal)
            table.setCellWidget(row, 5, self._action_cell(acct, inactive))
            table.setRowHeight(row, ROW_H)

        count = len(accounts)
        if inactive:
            total_label.setText(
                f"{count} inactive account{'s' if count != 1 else ''}")
        else:
            sign = "$" if total >= 0 else "-$"
            total_label.setText(
                f"{count} account{'s' if count != 1 else ''}  —  "
                f"Net balance: {sign}{abs(total):,.2f}")

    def _action_cell(self, acct, inactive: bool) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)

        _CELL_BTN = "QPushButton { padding: 4px 12px; font-size: 13px; font-weight: 600; border-radius: 5px; }"
        _WHITE = _CELL_BTN + "QPushButton { background: white; color: #1a1a2e; border: 1px solid #e5e7eb; } QPushButton:hover { background: #f1f5f9; }"

        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("Secondary")
        edit_btn.setStyleSheet(_WHITE)
        edit_btn.clicked.connect(lambda _, a=acct: self._edit(a))
        lay.addWidget(edit_btn)

        if inactive:
            react_btn = QPushButton("Reactivate")
            react_btn.setObjectName("Success")
            react_btn.setStyleSheet(_CELL_BTN + "QPushButton { background: #16a34a; color: white; border: none; } QPushButton:hover { background: #15803d; }")
            react_btn.clicked.connect(lambda _, a=acct: self._reactivate(a))
            lay.addWidget(react_btn)
        else:
            del_btn = QPushButton("Delete")
            del_btn.setObjectName("Danger")
            del_btn.setStyleSheet(_CELL_BTN + "QPushButton { background: #ef4444; color: white; border: none; } QPushButton:hover { background: #dc2626; }")
            del_btn.clicked.connect(lambda _, a=acct: self._delete(a))
            lay.addWidget(del_btn)

        return cell

    def _add(self):
        from ui.dialogs.account_dialog import AccountDialog
        dlg = AccountDialog(institutions=self.db.get_institutions(), parent=self)
        if dlg.exec() == AccountDialog.DialogCode.Accepted:
            self.db.save_account(dlg.get_data())
            self.refresh()

    def _edit(self, acct):
        from ui.dialogs.account_dialog import AccountDialog
        balance = self.db.account_balance(acct["id"])
        dlg = AccountDialog(account=acct, institutions=self.db.get_institutions(),
                            balance=balance, parent=self)
        if dlg.exec() == AccountDialog.DialogCode.Accepted:
            self.db.save_account(dlg.get_data())
            self.refresh()

    def _reactivate(self, acct):
        self.db.set_account_active(acct["id"], True)
        self.refresh()

    def _delete(self, acct):
        reply = QMessageBox.question(
            self, "Delete Account",
            f"Delete '{acct['name']}'?\n\nTransactions will remain in the system.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_account(acct["id"])
            self.refresh()
