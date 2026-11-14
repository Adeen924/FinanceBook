"""Settings → "Check for Flipped Imported Transactions" (v1.15).

Before 1.15 an imported withdrawal could end up saved as a deposit (or the
other way round): picking an account for an uncategorized imported entry in the
Transaction dialog flipped its sign, and CSV files whose Debit column held
negative numbers imported withdrawals as deposits. Nothing is changed
automatically — a real refund looks exactly the same — so this lists imported
entries whose sign disagrees with their account type and lets the user flip the
ones that are wrong.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QHeaderView, QTableWidgetItem, QMessageBox, QWidget)
from PyQt6.QtCore import Qt

from ui.widgets import DataTable, SecondaryButton, fmt_date
from ui.styles import WARNING

COL_PICK, COL_DATE, COL_BANK, COL_PAYEE, COL_ACCOUNT, COL_AMOUNT, COL_ACTION = range(7)


class SignCheckDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.changed = False
        self.setWindowTitle("Check for Flipped Imported Transactions")
        self.resize(1000, 560)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "These imported transactions have a sign that doesn't match their account: "
            "money in filed under an expense account, or money out under an income "
            "account. Some are genuine refunds or reversals — leave those alone. Flip "
            "the ones that went the wrong way (compare with your bank statement).")
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        lay.addWidget(intro)

        self.table = DataTable(["", "Date", "Bank / Card", "Payee / Memo", "Account",
                                "Amount", ""])
        self.table.setColumnWidth(COL_PICK, 34)
        self.table.setColumnWidth(COL_DATE, 100)
        self.table.setColumnWidth(COL_BANK, 150)
        self.table.horizontalHeader().setSectionResizeMode(COL_PAYEE,
                                                           QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_ACCOUNT, 200)
        self.table.setColumnWidth(COL_AMOUNT, 110)
        self.table.setColumnWidth(COL_ACTION, 110)
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.select_all_btn = SecondaryButton("Select All")
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        row.addWidget(self.select_all_btn)
        self.select_none_btn = SecondaryButton("Select None")
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))
        row.addWidget(self.select_none_btn)
        row.addStretch()
        self.flip_selected_btn = QPushButton("Flip All Selected")
        self.flip_selected_btn.clicked.connect(self._flip_selected)
        row.addWidget(self.flip_selected_btn)
        close_btn = SecondaryButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self.refresh()

    # ── loading ────────────────────────────────────────────────────────────────

    def refresh(self):
        self._rows = self.db.find_flipped_imports()
        accts = {a["id"]: a for a in self.db.get_accounts(active=None)}
        lock = self.db.lock_date()
        ro = bool(getattr(self.db, "read_only", False))
        self.table.clear_rows()
        self.table.setRowCount(len(self._rows))
        locked_count = 0
        for r, t in enumerate(self._rows):
            locked = bool(lock) and (t.get("date") or "") <= lock
            locked_count += locked
            pick = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            if not (locked or ro):
                flags |= Qt.ItemFlag.ItemIsEnabled
            pick.setFlags(flags)
            pick.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(r, COL_PICK, pick)
            self.table.set_item(r, COL_DATE, fmt_date(t.get("date", "")))
            self.table.set_item(r, COL_BANK, accts.get(t.get("account_id", ""), {}).get("name", "—"))
            self.table.set_item(r, COL_PAYEE, t.get("payee") or t.get("memo") or "—")
            kind = ("expense" if t.get("category_type") in self.db._SPENDING_TYPES
                    else "income")
            self.table.set_item(r, COL_ACCOUNT, f"{t.get('category_name', '')} ({kind})")
            self.table.money_item(r, COL_AMOUNT, float(t.get("amount") or 0))
            if locked:
                self.table.set_item(r, COL_ACTION, "🔒 Locked", color=WARNING)
            else:
                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(4, 3, 4, 3)
                btn = QPushButton("Flip sign")
                btn.setObjectName("CellButton")
                btn.setEnabled(not ro)
                btn.clicked.connect(lambda _, tid=t["id"]: self._flip([tid]))
                cl.addWidget(btn)
                self.table.setCellWidget(r, COL_ACTION, cell)
            self.table.setRowHeight(r, 38)

        n = len(self._rows)
        if not n:
            msg = "✓ No imported transactions with a mismatched sign."
        else:
            msg = f"{n} transaction{'s' if n != 1 else ''} to review."
            if locked_count:
                msg += (f" {locked_count} {'is' if locked_count == 1 else 'are'} in a locked "
                        "period — unlock it in Settings → Period Lock to change "
                        f"{'it' if locked_count == 1 else 'them'}.")
        if ro:
            msg += "  This database is open read-only, so nothing can be changed here."
        self.status.setText(msg)
        for b in (self.flip_selected_btn, self.select_all_btn, self.select_none_btn):
            b.setEnabled(bool(n) and not ro)
            b.setToolTip("This database is open read-only." if ro else "")

    # ── actions ────────────────────────────────────────────────────────────────

    def _set_all(self, on: bool):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_PICK)
            if it is not None and it.flags() & Qt.ItemFlag.ItemIsEnabled:
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def selected_ids(self) -> list:
        out = []
        for r, t in enumerate(self._rows):
            it = self.table.item(r, COL_PICK)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(t["id"])
        return out

    def _flip_selected(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Nothing Selected",
                                    "Tick the transactions to flip first.")
            return
        if QMessageBox.question(
                self, "Flip Signs",
                f"Reverse the sign of {len(ids)} transaction{'s' if len(ids) != 1 else ''} "
                "(money in ↔ money out)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self._flip(ids)

    def _flip(self, ids):
        try:
            flipped, locked = self.db.flip_transaction_signs(ids)
        except Exception as e:      # e.g. read-only or a lock set elsewhere
            QMessageBox.warning(self, "Couldn't Flip", str(e))
            return
        self.changed = self.changed or bool(flipped)
        self.refresh()
        if locked:
            QMessageBox.information(self, "Locked Period",
                                    f"Flipped {flipped}; skipped {locked} in a locked period.")
