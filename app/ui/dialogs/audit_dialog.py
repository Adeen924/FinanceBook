"""Audit log viewer: who changed what, and when."""
import csv
import json

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit,
                             QPushButton, QHeaderView, QDialogButtonBox, QFileDialog, QLabel)

from ui.widgets import DataTable, SecondaryButton
from sheets.admin_store import AUDITED_TABLES
from ui.styles import MUTED

_SHOW = ("date", "payee", "name", "amount", "memo", "category_id", "account_id", "status", "number")


def summarize(entry: dict) -> str:
    """Short human summary of an audit entry's details."""
    try:
        d = json.loads(entry.get("details") or "{}")
    except ValueError:
        return ""
    old, new = d.get("old") or {}, d.get("new") or {}
    if entry["op"] == "update":
        changes = [f"{k}: {old.get(k)!r} → {new.get(k)!r}" for k in new
                   if old.get(k) != new.get(k) and k not in ("updated_at", "updated_by", "created_by")]
        return "; ".join(changes[:6])
    row = new or old
    return ", ".join(f"{k}={row[k]}" for k in _SHOW if row.get(k) not in (None, ""))[:200]


class AuditDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Audit Log")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.table_combo = QComboBox()
        self.table_combo.addItem("Everything", "")
        for t in AUDITED_TABLES:
            self.table_combo.addItem(t.replace("_", " ").title(), t)
        self.table_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.table_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search (payee, amount, user, id…)")
        self.search.returnPressed.connect(self.refresh)
        row.addWidget(self.search, 1)
        go = QPushButton("Search")
        go.clicked.connect(self.refresh)
        row.addWidget(go)
        exp = SecondaryButton("Export CSV")
        exp.clicked.connect(self._export)
        row.addWidget(exp)
        lay.addLayout(row)
        self.grid = DataTable(["When", "Who", "What", "Action", "Record", "Details"])
        hh = self.grid.horizontalHeader()
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 150), (1, 110), (2, 110), (3, 70), (4, 90)):
            self.grid.setColumnWidth(c, w)
        lay.addWidget(self.grid, 1)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        lay.addWidget(self.count)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.refresh()

    def refresh(self, *_):
        self._rows = self.db.get_audit_log(self.table_combo.currentData() or "",
                                           self.search.text().strip())
        g = self.grid
        g.clear_rows()
        g.setRowCount(len(self._rows))
        for r, e in enumerate(self._rows):
            g.set_item(r, 0, e["ts"])
            g.set_item(r, 1, e["user"] or "—")
            g.set_item(r, 2, e["table_name"])
            g.set_item(r, 3, e["op"], bold=True)
            g.set_item(r, 4, e["row_id"], color=MUTED)
            item = g.set_item(r, 5, summarize(e))
            item.setToolTip(e.get("details", "")[:2000])
        self.count.setText(f"{len(self._rows)} entries (newest first, up to 2,000 shown)")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Audit Log", "audit_log.csv",
                                              "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["When", "Who", "Table", "Action", "Record", "Details"])
            for e in self._rows:
                w.writerow([e["ts"], e["user"], e["table_name"], e["op"], e["row_id"], e["details"]])
