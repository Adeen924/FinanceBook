"""Reconciliation — account selection + reconcile session."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QMessageBox, QComboBox, QDoubleSpinBox,
                              QDateEdit, QCheckBox, QScrollArea, QFrame,
                              QFormLayout, QHeaderView, QGroupBox, QStackedWidget)
from PyQt6.QtCore import Qt, QDate
from ui.widgets import PageTitle, DataTable, SecondaryButton, SuccessButton
from ui.styles  import SUCCESS, DANGER, WARNING


class ReconcilePage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        self._stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.addWidget(self._stack)

        # Page 0 — account selector
        self._sel_page = QWidget()
        self._build_selector()
        self._stack.addWidget(self._sel_page)

        # Page 1 — reconcile session
        self._sess_page = QWidget()
        self._build_session()
        self._stack.addWidget(self._sess_page)

    def _build_selector(self):
        lay = QVBoxLayout(self._sel_page)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)
        lay.addWidget(PageTitle("Reconcile"))

        info = QLabel("Compare your FinanceBook records against your bank statement.\n"
                      "Check off each transaction that appears on the statement until the difference is $0.00.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._acct_table = DataTable(
            ["Account", "Type", "Book Balance", "Cleared Balance", "Last Reconciled", "Action"])
        self._acct_table.setColumnWidth(0, 200)
        self._acct_table.setColumnWidth(1, 100)
        self._acct_table.setColumnWidth(2, 130)
        self._acct_table.setColumnWidth(3, 130)
        self._acct_table.setColumnWidth(4, 140)
        self._acct_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._acct_table.setColumnWidth(5, 130)
        self._acct_table.verticalHeader().setDefaultSectionSize(44)
        lay.addWidget(self._acct_table)

    def _build_session(self):
        lay = QVBoxLayout(self._sess_page)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        # Back button + title
        hdr = QHBoxLayout()
        back_btn = SecondaryButton("← Back")
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        hdr.addWidget(back_btn)
        self._sess_title = QLabel("Reconcile")
        self._sess_title.setObjectName("PageTitle")
        hdr.addWidget(self._sess_title)
        hdr.addStretch()
        lay.addLayout(hdr)

        # Statement info bar
        stmt_grp = QGroupBox("Statement Information")
        stmt_lay = QFormLayout(stmt_grp)
        stmt_lay.setSpacing(8)

        self._stmt_date = QDateEdit()
        self._stmt_date.setCalendarPopup(True)
        self._stmt_date.setDisplayFormat("yyyy-MM-dd")
        self._stmt_date.setDate(QDate.currentDate())
        stmt_lay.addRow("Statement Date:", self._stmt_date)

        self._stmt_bal = QDoubleSpinBox()
        self._stmt_bal.setRange(-9_999_999, 9_999_999)
        self._stmt_bal.setDecimals(2)
        self._stmt_bal.setPrefix("$")
        self._stmt_bal.setSingleStep(1.0)
        self._stmt_bal.valueChanged.connect(self._update_diff)
        stmt_lay.addRow("Statement Ending Balance:", self._stmt_bal)
        lay.addWidget(stmt_grp)

        # Running totals bar
        totals = QHBoxLayout()
        totals.setSpacing(30)
        self._cleared_lbl = QLabel("Cleared: $0.00")
        self._cleared_lbl.setStyleSheet("font-size:14px; font-weight:bold;")
        self._diff_lbl = QLabel("Difference: $0.00")
        self._diff_lbl.setStyleSheet("font-size:14px; font-weight:bold;")
        totals.addWidget(self._cleared_lbl)
        totals.addWidget(self._diff_lbl)
        totals.addStretch()
        self._complete_btn = SuccessButton("✓ Complete Reconciliation")
        self._complete_btn.setEnabled(False)
        self._complete_btn.clicked.connect(self._complete)
        totals.addWidget(self._complete_btn)
        lay.addLayout(totals)

        # Transaction table
        check_row = QHBoxLayout()
        check_all_btn = SecondaryButton("Check All")
        check_all_btn.clicked.connect(lambda: self._check_all(True))
        uncheck_btn = SecondaryButton("Uncheck All")
        uncheck_btn.clicked.connect(lambda: self._check_all(False))
        check_row.addWidget(check_all_btn)
        check_row.addWidget(uncheck_btn)
        check_row.addStretch()
        lay.addLayout(check_row)

        self._sess_table = DataTable(
            ["✓", "Date", "Payee / Memo", "Payment (Out)", "Deposit (In)"])
        self._sess_table.setColumnWidth(0, 36)
        self._sess_table.setColumnWidth(1, 100)
        self._sess_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._sess_table.setColumnWidth(3, 120)
        self._sess_table.setColumnWidth(4, 120)
        lay.addWidget(self._sess_table)

        self._current_account_id = ""
        self._session_txns: list[dict] = []
        self._checkboxes: list[QCheckBox] = []

    def refresh(self):
        self._stack.setCurrentIndex(0)
        self._load_selector()

    def _load_selector(self):
        accounts = self.db.get_accounts()
        self._acct_table.clear_rows()
        self._acct_table.setRowCount(len(accounts))
        for row, acct in enumerate(accounts):
            bal     = self.db.account_balance(acct["id"])
            cleared = self.db.cleared_balance(acct["id"])
            recs    = self.db.get_reconciliations(acct["id"])
            last    = recs[0]["statement_date"] if recs else "Never"

            self._acct_table.set_item(row, 0, acct["name"], bold=True)
            self._acct_table.set_item(row, 1, acct.get("type","").title())
            self._acct_table.money_item(row, 2, bal)
            self._acct_table.money_item(row, 3, cleared)
            self._acct_table.set_item(row, 4, last)

            from PyQt6.QtWidgets import QWidget as W, QHBoxLayout as HL
            cell = W(); cl = HL(cell); cl.setContentsMargins(6,5,6,5)
            btn = QPushButton("Reconcile")
            btn.setStyleSheet("QPushButton { padding: 4px 12px; font-size: 13px; font-weight: 600; border-radius: 5px; background: #2979ff; color: white; border: none; } QPushButton:hover { background: #1565c0; }")
            btn.clicked.connect(lambda _, a=acct: self._start_session(a))
            cl.addWidget(btn)
            self._acct_table.setCellWidget(row, 5, cell)
            self._acct_table.setRowHeight(row, 44)

    def _start_session(self, acct: dict):
        self._current_account_id = acct["id"]
        self._sess_title.setText(f"Reconcile — {acct['name']}")

        # Pre-fill last statement balance
        recs = self.db.get_reconciliations(acct["id"])
        if recs:
            self._stmt_bal.setValue(float(recs[0].get("statement_balance") or 0))

        # Load unreconciled transactions
        self._session_txns = [
            t for t in self.db.get_transactions(account_id=acct["id"])
            if str(t.get("reconciled","0")) == "0"
        ]
        self._session_txns.sort(key=lambda t: t.get("date",""))

        self._sess_table.clear_rows()
        self._sess_table.setRowCount(len(self._session_txns))
        self._checkboxes = []

        for row, t in enumerate(self._session_txns):
            amt = float(t.get("amount") or 0)

            cb = QCheckBox()
            cb.stateChanged.connect(self._update_diff)
            from PyQt6.QtWidgets import QWidget as W, QHBoxLayout as HL
            cell = W(); cl = HL(cell); cl.setContentsMargins(8,0,0,0)
            cl.addWidget(cb)
            self._sess_table.setCellWidget(row, 0, cell)
            self._checkboxes.append(cb)

            self._sess_table.set_item(row, 1, t.get("date",""))
            self._sess_table.set_item(row, 2, t.get("payee") or t.get("memo","—"))
            if amt < 0:
                self._sess_table.money_item(row, 3, abs(amt))
            else:
                self._sess_table.money_item(row, 4, amt)
            self._sess_table.setRowHeight(row, 30)

        self._update_diff()
        self._stack.setCurrentIndex(1)

    def _update_diff(self):
        cleared_base = self.db.cleared_balance(self._current_account_id)
        checked_sum = sum(
            float(self._session_txns[i].get("amount") or 0)
            for i, cb in enumerate(self._checkboxes)
            if cb.isChecked()
        )
        cleared_total = cleared_base + checked_sum
        stmt_bal = self._stmt_bal.value()
        diff = cleared_total - stmt_bal

        self._cleared_lbl.setText(f"Cleared: ${cleared_total:,.2f}")
        color = SUCCESS if abs(diff) < 0.005 else DANGER
        self._diff_lbl.setText(f"Difference: ${diff:+,.2f}")
        self._diff_lbl.setStyleSheet(f"font-size:14px; font-weight:bold; color:{color};")
        self._complete_btn.setEnabled(abs(diff) < 0.005)

    def _check_all(self, state: bool):
        for cb in self._checkboxes:
            cb.setChecked(state)
        self._update_diff()

    def _complete(self):
        cleared_ids = [
            self._session_txns[i]["id"]
            for i, cb in enumerate(self._checkboxes)
            if cb.isChecked()
        ]
        rec = self.db.save_reconciliation({
            "account_id": self._current_account_id,
            "statement_date": self._stmt_date.date().toString("yyyy-MM-dd"),
            "statement_balance": str(self._stmt_bal.value()),
            "cleared_balance": str(self.db.cleared_balance(self._current_account_id)
                                   + sum(float(self._session_txns[i].get("amount") or 0)
                                         for i, cb in enumerate(self._checkboxes) if cb.isChecked())),
            "difference": "0",
        })
        self.db.complete_reconciliation(rec["id"], cleared_ids)
        QMessageBox.information(self, "Reconciliation Complete",
            f"Reconciliation complete!\n{len(cleared_ids)} transactions marked as reconciled.")
        self._stack.setCurrentIndex(0)
        self._load_selector()
