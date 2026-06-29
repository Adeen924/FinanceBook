"""Loans page — create and track amortizing loans."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QMessageBox, QHeaderView, QLabel, QDialog,
                              QTableWidget, QTableWidgetItem, QAbstractItemView,
                              QSizePolicy, QFrame)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from ui.widgets import PageTitle, DataTable
from utils.loan import amortization_schedule, calc_monthly_payment, remaining_balance


ROW_H = 44


class LoansPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Loans"))
        hdr.addStretch()
        add_btn = QPushButton("+ Add Loan")
        add_btn.clicked.connect(self._add)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        note = QLabel(
            "Track amortizing loans here. When you record a loan payment as a transaction, "
            "select the loan and the app will automatically split it into principal (reduces "
            "balance) and interest (expense)."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._table = DataTable(
            ["Name", "Lender", "Original Amount", "Rate", "Term",
             "Monthly Payment", "Remaining Balance", "Actions"])

        self._table.setColumnWidth(0, 200)
        self._table.setColumnWidth(1, 140)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 80)
        self._table.setColumnWidth(5, 130)
        self._table.setColumnWidth(6, 140)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(7, 200)

        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        lay.addWidget(self._table)

    def refresh(self):
        loans = self.db.get_loans()
        self._table.clear_rows()
        self._table.setRowCount(len(loans))

        for row, loan in enumerate(loans):
            paid_txns = self.db.get_loan_transactions(loan["id"])
            rem       = remaining_balance(loan, paid_txns)
            original  = float(loan.get("original_principal") or 0)
            rate_pct  = float(loan.get("annual_rate") or 0) * 100
            term      = int(loan.get("term_months") or 0)
            pmt       = float(loan.get("payment_amount") or 0) or calc_monthly_payment(
                            original, float(loan.get("annual_rate") or 0), term)

            self._table.set_item(row, 0, loan.get("name", ""), bold=True)
            self._table.set_item(row, 1, loan.get("lender", "") or "—")
            self._table.money_item(row, 2, original)
            self._table.set_item(row, 3, f"{rate_pct:.3f}%",
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._table.set_item(row, 4,
                                 f"{term} mo" if term < 240 else f"{term // 12} yr",
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._table.money_item(row, 5, pmt)

            rem_item = self._table.money_item(row, 6, rem)
            if rem_item and rem < original * 0.1:
                rem_item.setForeground(QColor("#16a34a"))

            self._table.setCellWidget(row, 7, self._action_cell(loan))
            self._table.setRowHeight(row, ROW_H)

    # ── action cell ───────────────────────────────────────────────────────────

    def _action_cell(self, loan) -> QWidget:
        cell = QWidget()
        lay  = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)

        _BTN = ("QPushButton { padding: 4px 10px; font-size: 12px; font-weight: 600; "
                "border-radius: 5px; }")

        sched_btn = QPushButton("Schedule")
        sched_btn.setObjectName("Secondary")
        sched_btn.setStyleSheet(_BTN + "QPushButton { background: white; color: #1a1a2e; "
                                       "border: 1px solid #e5e7eb; } "
                                       "QPushButton:hover { background: #f1f5f9; }")
        sched_btn.clicked.connect(lambda _, ln=loan: self._show_schedule(ln))

        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("Secondary")
        edit_btn.setStyleSheet(_BTN + "QPushButton { background: white; color: #1a1a2e; "
                                      "border: 1px solid #e5e7eb; } "
                                      "QPushButton:hover { background: #f1f5f9; }")
        edit_btn.clicked.connect(lambda _, ln=loan: self._edit(ln))

        del_btn = QPushButton("Delete")
        del_btn.setObjectName("Danger")
        del_btn.setStyleSheet(_BTN + "QPushButton { background: #ef4444; color: white; "
                                     "border: none; } "
                                     "QPushButton:hover { background: #dc2626; }")
        del_btn.clicked.connect(lambda _, ln=loan: self._delete(ln))

        lay.addWidget(sched_btn)
        lay.addWidget(edit_btn)
        lay.addWidget(del_btn)
        return cell

    # ── amortization schedule dialog ──────────────────────────────────────────

    def _show_schedule(self, loan):
        paid_txns = self.db.get_loan_transactions(loan["id"])
        paid_set  = {int(t.get("principal_amount") or 0) for t in paid_txns}  # rough paid count
        paid_nums = set()
        for t in paid_txns:
            # We don't store payment_number on the txn, so mark by date match below
            pass

        schedule = amortization_schedule(loan)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Amortization Schedule — {loan['name']}")
        dlg.resize(760, 560)
        lay = QVBoxLayout(dlg)

        # Summary strip
        original = float(loan.get("original_principal") or 0)
        rem      = remaining_balance(loan, paid_txns)
        paid_ct  = len(paid_txns)
        info     = QLabel(
            f"Original: ${original:,.2f}   |   "
            f"Payments made: {paid_ct}   |   "
            f"Remaining balance: ${rem:,.2f}"
        )
        info.setObjectName("Muted")
        lay.addWidget(info)

        tbl = QTableWidget(len(schedule), 6)
        tbl.setHorizontalHeaderLabels(
            ["#", "Due Date", "Payment", "Principal", "Interest", "Balance"])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.verticalHeader().hide()

        # Build a set of paid due dates for highlighting
        paid_dates = {t.get("date","") for t in paid_txns}

        for r, entry in enumerate(schedule):
            is_paid = entry["due_date"] in paid_dates

            def _cell(text, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter):
                item = QTableWidgetItem(text)
                item.setTextAlignment(align)
                if is_paid:
                    item.setBackground(QColor("#dcfce7"))
                return item

            tbl.setItem(r, 0, _cell(str(entry["payment_number"]),
                                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
            tbl.setItem(r, 1, _cell(entry["due_date"],
                                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
            tbl.setItem(r, 2, _cell(f"${entry['payment']:,.2f}"))
            tbl.setItem(r, 3, _cell(f"${entry['principal']:,.2f}"))
            tbl.setItem(r, 4, _cell(f"${entry['interest']:,.2f}"))
            tbl.setItem(r, 5, _cell(f"${entry['balance']:,.2f}"))

        lay.addWidget(tbl)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def _add(self):
        from ui.dialogs.loan_dialog import LoanDialog
        dlg = LoanDialog(self.db, parent=self)
        if dlg.exec() == LoanDialog.DialogCode.Accepted:
            self.db.save_loan(dlg.get_data())
            self.refresh()

    def _edit(self, loan):
        from ui.dialogs.loan_dialog import LoanDialog
        dlg = LoanDialog(self.db, loan=loan, parent=self)
        if dlg.exec() == LoanDialog.DialogCode.Accepted:
            self.db.save_loan(dlg.get_data())
            self.refresh()

    def _delete(self, loan):
        linked = self.db.get_loan_transactions(loan["id"])
        msg = f"Delete loan '{loan['name']}'?"
        if linked:
            msg += f"\n\n{len(linked)} transaction(s) are linked to this loan. " \
                   "They will remain but will lose their loan split data."
        reply = QMessageBox.question(self, "Delete Loan", msg,
                                     QMessageBox.StandardButton.Yes |
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_loan(loan["id"])
            self.refresh()
