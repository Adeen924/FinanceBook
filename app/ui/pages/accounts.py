from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QMessageBox, QHeaderView, QLabel)
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

        self._table = DataTable(
            ["Name", "Type", "Institution", "Currency", "Balance", "Actions"])

        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 200)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 150)

        # Actions column: fixed width, never resized by stretch
        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 170)

        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        lay.addWidget(self._table)

        self._total_label = QLabel("")
        self._total_label.setObjectName("Muted")
        lay.addWidget(self._total_label)

    def refresh(self):
        accounts = self.db.get_accounts()
        self._table.clear_rows()
        self._table.setRowCount(len(accounts))

        total = 0.0
        for row, acct in enumerate(accounts):
            bal = self.db.account_balance(acct["id"])
            total += bal

            self._table.set_item(row, 0, acct.get("name", ""), bold=True)
            self._table.set_item(row, 1, acct.get("type", "").title())
            self._table.set_item(row, 2, acct.get("institution", "") or "—")
            self._table.set_item(row, 3, acct.get("currency", "USD"),
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._table.money_item(row, 4, bal)
            self._table.setCellWidget(row, 5, self._action_cell(acct))
            self._table.setRowHeight(row, ROW_H)

        count = len(accounts)
        sign = "$" if total >= 0 else "-$"
        self._total_label.setText(
            f"{count} account{'s' if count != 1 else ''}  —  "
            f"Net balance: {sign}{abs(total):,.2f}"
        )

    def _action_cell(self, acct) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)

        _CELL_BTN = "QPushButton { padding: 4px 12px; font-size: 13px; font-weight: 600; border-radius: 5px; }"

        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("Secondary")
        edit_btn.setStyleSheet(_CELL_BTN + "QPushButton { background: white; color: #1a1a2e; border: 1px solid #e5e7eb; } QPushButton:hover { background: #f1f5f9; }")
        edit_btn.clicked.connect(lambda _, a=acct: self._edit(a))

        del_btn = QPushButton("Delete")
        del_btn.setObjectName("Danger")
        del_btn.setStyleSheet(_CELL_BTN + "QPushButton { background: #ef4444; color: white; border: none; } QPushButton:hover { background: #dc2626; }")
        del_btn.clicked.connect(lambda _, a=acct: self._delete(a))

        lay.addWidget(edit_btn)
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
        dlg = AccountDialog(account=acct, institutions=self.db.get_institutions(), parent=self)
        if dlg.exec() == AccountDialog.DialogCode.Accepted:
            self.db.save_account(dlg.get_data())
            self.refresh()

    def _delete(self, acct):
        reply = QMessageBox.question(
            self, "Delete Account",
            f"Delete '{acct['name']}'?\n\nTransactions will remain in the system.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_account(acct["id"])
            self.refresh()
