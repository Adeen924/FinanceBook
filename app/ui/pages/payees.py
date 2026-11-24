"""
Payees (v1.19) — one implementation for both book types.

A payee is a contact (the contacts table, linked by transactions.contact_id).
Personal books show PayeesPage in the sidebar (MONEY → Payees); business books
show the same PayeesPanel as the "Payees" tab of the Contacts page.

The list shows each payee's number of transactions (a split counts once),
total spent / received and when it was last used; double-click opens
Transactions filtered to that payee. Add, rename, merge and make inactive.

"Unlinked payee names" lists the payee texts on transactions with no payee
linked, grouped so that case, spacing and store numbers don't matter
("WALMART #1234" and "Walmart Store 5678" are one name), with
"Create payee & link N transactions" after a preview of what will be linked.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QCheckBox, QInputDialog, QMessageBox, QDialog,
                             QDialogButtonBox, QListWidget, QListWidgetItem, QLineEdit)
from PyQt6.QtCore import Qt

from ui.widgets import PageTitle, DataTable, SecondaryButton, fmt_date
from ui.styles import MUTED

ROW_H = 38


def _money(v: float) -> str:
    return f"${v:,.2f}" if v else "—"


class LinkPreviewDialog(QDialog):
    """Preview before "Create payee & link": the name to create and every
    transaction that will be linked."""
    def __init__(self, group: dict, txns: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Payee & Link Transactions")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Payee name:"))
        self.name = QLineEdit(group.get("name", ""))
        lay.addWidget(self.name)
        n = group.get("count", len(txns))
        info = QLabel(f"These {n} transaction{'s' if n != 1 else ''} will be linked "
                      f"(bank names: {', '.join(group.get('variants', [])[:5])}"
                      f"{'…' if len(group.get('variants', [])) > 5 else ''}). Future imports "
                      "whose payee matches are linked automatically.")
        info.setWordWrap(True)
        info.setObjectName("Muted")
        lay.addWidget(info)
        self.table = DataTable(["Date", "Payee", "Amount"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(txns))
        for r, t in enumerate(txns):
            self.table.set_item(r, 0, fmt_date(t.get("date", "")))
            self.table.set_item(r, 1, t.get("payee", ""))
            self.table.money_item(r, 2, float(t.get("amount") or 0))
        lay.addWidget(self.table, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText(
            f"Create payee && link {n}")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


class MergeDialog(QDialog):
    """Pick the payees to merge into one."""
    def __init__(self, keep: dict, payees: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Merge into “{keep['name']}”")
        self.resize(420, 440)
        lay = QVBoxLayout(self)
        lbl = QLabel(f"Tick the payees to merge into <b>{keep['name']}</b>. Their "
                     "transactions move to it and they become inactive.")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.list = QListWidget()
        for p in payees:
            if p["id"] == keep["id"]:
                continue
            it = QListWidgetItem(f"{p['name']}  ({p.get('n_txns', 0)})")
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def chosen(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class PayeesPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._rows: list[dict] = []
        self._unlinked: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        self._show_inactive = QCheckBox("Show inactive")
        self._show_inactive.toggled.connect(self.refresh)
        bar.addWidget(self._show_inactive)
        bar.addStretch()
        for text, fn, tip in (
                ("+ Add Payee", self._add, "A new payee"),
                ("Rename…", self._rename, "Rename the selected payee"),
                ("Merge…", self._merge, "Merge other payees into the selected one"),
                ("Make Inactive", self._toggle_active,
                 "Hide the selected payee from lists (history is kept)")):
            b = QPushButton(text) if text.startswith("+") else SecondaryButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
            if text == "Make Inactive":
                self._active_btn = b
        lay.addLayout(bar)

        self.table = DataTable(["Payee", "Transactions", "Spent", "Received", "Last used"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 110), (2, 120), (3, 120), (4, 110)):
            self.table.setColumnWidth(c, w)
        self.table.setToolTip("Double-click a payee to see its transactions")
        self.table.doubleClicked.connect(lambda i: self.open_transactions(i.row()))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table, 3)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        lay.addWidget(self.summary)

        head = QLabel("<b>Unlinked payee names</b> — payee texts on transactions with no "
                      "payee yet (case, spacing and store numbers ignored)")
        head.setWordWrap(True)
        lay.addWidget(head)
        self.unlinked = DataTable(["Name", "Bank texts", "Transactions", ""])
        uh = self.unlinked.horizontalHeader()
        uh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.unlinked.setColumnWidth(0, 200)
        self.unlinked.setColumnWidth(2, 100)
        uh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.unlinked.setColumnWidth(3, 230)
        uh.setStretchLastSection(False)
        lay.addWidget(self.unlinked, 2)

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self, *_):
        self._rows = self.db.payee_summary(include_inactive=self._show_inactive.isChecked())
        t = self.table
        t.clear_rows()
        t.setRowCount(len(self._rows))
        for r, p in enumerate(self._rows):
            name = p["name"] + ("" if str(p.get("active", 1)) == "1" else "  (inactive)")
            t.set_item(r, 0, name, bold=True, color=None if str(p.get("active", 1)) == "1"
                       else MUTED)
            t.set_item(r, 1, str(p["n_txns"]),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 2, _money(p["spent"]),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 3, _money(p["received"]),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 4, fmt_date(p["last_used"]) if p["last_used"] else "—")
            t.setRowHeight(r, ROW_H)
        self.summary.setText(f"{len(self._rows)} payees · double-click one to see its "
                             "transactions")

        self._unlinked = self.db.unlinked_payee_names()
        u = self.unlinked
        u.clear_rows()
        u.setRowCount(len(self._unlinked))
        for r, g in enumerate(self._unlinked):
            u.set_item(r, 0, g["name"], bold=True)
            u.set_item(r, 1, ", ".join(g["variants"][:4]) + ("…" if len(g["variants"]) > 4 else ""),
                       color=MUTED)
            u.set_item(r, 2, str(g["count"]),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(6, 3, 6, 3)
            b = QPushButton(f"Create payee && link {g['count']}")
            b.setObjectName("CellButton")
            b.clicked.connect(lambda _, x=g: self.create_and_link(x))
            cl.addWidget(b)
            u.setCellWidget(r, 3, cell)
            u.setRowHeight(r, ROW_H)
        self._update_buttons()

    def _selected(self) -> dict | None:
        r = self.table.currentRow()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def _update_buttons(self):
        p = self._selected()
        self._active_btn.setText("Make Active" if p and str(p.get("active", 1)) != "1"
                                 else "Make Inactive")

    # ── actions ──────────────────────────────────────────────────────────────

    def open_transactions(self, row: int):
        if not (0 <= row < len(self._rows)):
            return
        p = self._rows[row]
        mw = self.window()
        page = mw.page("Transactions") if hasattr(mw, "page") else None
        if page is not None and hasattr(page, "show_filtered"):
            page.show_filtered(contact_id=p["id"], label=f"Payee: {p['name']}")

    def _add(self):
        name, ok = QInputDialog.getText(self, "Add Payee", "Payee name:")
        if ok and name.strip():
            if self.db.find_payee_by_name(name):
                QMessageBox.information(self, "Already There",
                                        f"“{name.strip()}” is already a payee.")
                return
            self.db.add_payee(name)
            self.refresh()

    def _rename(self):
        p = self._selected()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Rename Payee", "New name:", text=p["name"])
        if ok and name.strip() and name.strip() != p["name"]:
            other = self.db.find_payee_by_name(name)
            if other and other["id"] != p["id"]:
                QMessageBox.information(self, "Name in Use",
                    f"“{name.strip()}” is another payee. Use Merge… to combine them.")
                return
            self.db.save_contact(dict(self.db.get_contact(p["id"]), name=name.strip()))
            self.refresh()

    def _merge(self):
        p = self._selected()
        if not p:
            QMessageBox.information(self, "Merge", "Select the payee to keep first.")
            return
        dlg = MergeDialog(p, self.db.payee_summary(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen():
            return
        n = self.db.merge_payees(p["id"], dlg.chosen())
        QMessageBox.information(self, "Merged",
                                f"Merged {len(dlg.chosen())} payee(s) into “{p['name']}” "
                                f"({n} transaction rows moved).")
        self.refresh()

    def _toggle_active(self):
        p = self._selected()
        if not p:
            return
        active = str(p.get("active", 1)) == "1"
        self.db.save_contact(dict(self.db.get_contact(p["id"]), active="0" if active else "1"))
        self.refresh()

    def create_and_link(self, group: dict, confirm=None):
        """Preview, then create the payee and link its transactions. confirm:
        test hook returning the payee name (or None to cancel)."""
        txns = self.db.transactions_for_payee_key(group["key"])
        if confirm is None:
            dlg = LinkPreviewDialog(group, txns, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            name = dlg.name.text().strip() or group["name"]
        else:
            name = confirm(group, txns)
            if not name:
                return None
        existing = self.db.find_payee_by_name(name)
        if existing:
            n = self.db.link_payee(existing["id"], [t["id"] for t in txns])
            payee = existing
        else:
            payee, n = self.db.create_payee_and_link(name, group["key"])
        self.refresh()
        return payee, n


class PayeesPage(QWidget):
    """Personal books: the Payees page (MONEY → Payees)."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        lay.addWidget(PageTitle("Payees"))
        self.panel = PayeesPanel(db, self)
        lay.addWidget(self.panel, 1)

    def refresh(self):
        self.panel.refresh()
