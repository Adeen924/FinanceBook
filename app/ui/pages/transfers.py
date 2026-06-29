"""Transfer detection & management."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QMessageBox, QTabWidget, QHeaderView)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, DataTable, SecondaryButton, DangerButton, SuccessButton
from ui.styles  import WARNING, SUCCESS


class TransfersPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._potential: list[dict] = []
        self._linked: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(PageTitle("Transfers Between Accounts"))

        info = QLabel(
            "Transfers are excluded from P&L reports. "
            "Link transactions that represent moving money between your own accounts.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._tabs = QTabWidget()

        # ── Potential tab ──
        pot_widget = QWidget()
        pot_lay = QVBoxLayout(pot_widget)
        pot_lay.setContentsMargins(8, 8, 8, 8)
        pot_lay.setSpacing(8)

        pot_hdr = QHBoxLayout()
        self._pot_label = QLabel("")
        self._pot_label.setObjectName("Muted")
        pot_hdr.addWidget(self._pot_label)
        pot_hdr.addStretch()
        rescan_btn = SecondaryButton("Re-scan")
        rescan_btn.clicked.connect(self.refresh)
        pot_hdr.addWidget(rescan_btn)
        pot_lay.addLayout(pot_hdr)

        self._pot_table = DataTable(
            ["Date (Out)", "From Account", "Payee", "Date (In)", "To Account", "Payee", "Amount", "Action"])
        self._pot_table.setColumnWidth(0, 95)
        self._pot_table.setColumnWidth(1, 140)
        self._pot_table.setColumnWidth(2, 150)
        self._pot_table.setColumnWidth(3, 95)
        self._pot_table.setColumnWidth(4, 140)
        self._pot_table.setColumnWidth(5, 150)
        self._pot_table.setColumnWidth(6, 100)
        self._pot_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._pot_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._pot_table.horizontalHeader().setSectionResizeMode(7, self._pot_table.horizontalHeader().ResizeMode.Fixed)
        self._pot_table.setColumnWidth(7, 80)
        self._pot_table.horizontalHeader().setStretchLastSection(False)
        self._pot_table.verticalHeader().setDefaultSectionSize(40)
        pot_lay.addWidget(self._pot_table)
        self._tabs.addTab(pot_widget, "Potential Transfers")

        # ── Linked tab ──
        lnk_widget = QWidget()
        lnk_lay = QVBoxLayout(lnk_widget)
        lnk_lay.setContentsMargins(8, 8, 8, 8)

        self._lnk_table = DataTable(
            ["Date", "Account", "Payee / Memo", "Amount", "Pair ID", "Action"])
        self._lnk_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._lnk_table.setColumnWidth(0, 100)
        self._lnk_table.setColumnWidth(1, 150)
        self._lnk_table.setColumnWidth(3, 110)
        self._lnk_table.setColumnWidth(4, 100)
        self._lnk_table.horizontalHeader().setSectionResizeMode(5, self._lnk_table.horizontalHeader().ResizeMode.Fixed)
        self._lnk_table.setColumnWidth(5, 80)
        self._lnk_table.horizontalHeader().setStretchLastSection(False)
        self._lnk_table.verticalHeader().setDefaultSectionSize(40)
        lnk_lay.addWidget(self._lnk_table)
        self._tabs.addTab(lnk_widget, "Linked Transfers")

        lay.addWidget(self._tabs)

    def refresh(self):
        accts = {a["id"]: a for a in self.db.get_accounts()}
        self._potential = self._detect(accts)
        self._linked    = [t for t in self.db.get_transactions(include_transfers=True)
                           if str(t.get("is_transfer","0")) == "1"]
        self._render_potential(accts)
        self._render_linked(accts)
        self._tabs.setTabText(0, f"Potential Transfers ({len(self._potential)})")
        self._tabs.setTabText(1, f"Linked Transfers ({len(self._linked)})")

    def _detect(self, accts: dict) -> list[dict]:
        from datetime import datetime
        txns = [t for t in self.db.get_transactions(include_transfers=True)
                if str(t.get("is_transfer","0")) == "0"]
        potential, seen = [], set()
        for i, t1 in enumerate(txns):
            if t1["id"] in seen:
                continue
            try:
                d1 = datetime.strptime(t1["date"], "%Y-%m-%d").date()
                a1 = float(t1.get("amount") or 0)
            except Exception:
                continue
            for t2 in txns[i+1:]:
                if t2["id"] in seen or t2["account_id"] == t1["account_id"]:
                    continue
                try:
                    d2 = datetime.strptime(t2["date"], "%Y-%m-%d").date()
                    a2 = float(t2.get("amount") or 0)
                except Exception:
                    continue
                if abs((d2-d1).days) <= 3 and abs(a1+a2) < 0.01 and a1 != 0:
                    seen.add(t1["id"]); seen.add(t2["id"])
                    potential.append({"txn_a": t1, "txn_b": t2, "amount": abs(a1)})
                    break
        return potential

    def _render_potential(self, accts):
        self._pot_table.clear_rows()
        self._pot_table.setRowCount(len(self._potential))
        for row, p in enumerate(self._potential):
            ta, tb = p["txn_a"], p["txn_b"]
            self._pot_table.set_item(row, 0, ta.get("date",""))
            self._pot_table.set_item(row, 1, accts.get(ta["account_id"],{}).get("name","—"))
            self._pot_table.set_item(row, 2, ta.get("payee") or ta.get("memo","—"))
            self._pot_table.set_item(row, 3, tb.get("date",""))
            self._pot_table.set_item(row, 4, accts.get(tb["account_id"],{}).get("name","—"))
            self._pot_table.set_item(row, 5, tb.get("payee") or tb.get("memo","—"))
            self._pot_table.money_item(row, 6, p["amount"])

            from PyQt6.QtWidgets import QWidget as W, QHBoxLayout as HL
            cell = W(); cl = HL(cell); cl.setContentsMargins(4,4,4,4)
            link_btn = SuccessButton("Link")
            link_btn.setFixedSize(52, 26)
            link_btn.clicked.connect(lambda _, r=row: self._link(r))
            cl.addStretch()
            cl.addWidget(link_btn)
            cl.addStretch()
            self._pot_table.setCellWidget(row, 7, cell)

        self._pot_label.setText(
            f"{len(self._potential)} possible transfer{'s' if len(self._potential) != 1 else ''} found "
            f"(same amount, opposite direction, ≤3 days apart)."
            if self._potential else "No potential transfers found — everything looks clean.")

    def _render_linked(self, accts):
        self._lnk_table.clear_rows()
        self._lnk_table.setRowCount(len(self._linked))
        for row, t in enumerate(self._linked):
            self._lnk_table.set_item(row, 0, t.get("date",""))
            self._lnk_table.set_item(row, 1, accts.get(t["account_id"],{}).get("name","—"))
            self._lnk_table.set_item(row, 2, t.get("payee") or t.get("memo","—"))
            self._lnk_table.money_item(row, 3, float(t.get("amount") or 0))
            self._lnk_table.set_item(row, 4, t.get("transfer_pair_id","")[:8])

            from PyQt6.QtWidgets import QWidget as W, QHBoxLayout as HL
            cell = W(); cl = HL(cell); cl.setContentsMargins(4,4,4,4)
            unlink_btn = DangerButton("✕")
            unlink_btn.setFixedSize(44, 26)
            unlink_btn.setToolTip("Unlink transfer")
            unlink_btn.clicked.connect(lambda _, tid=t["id"]: self._unlink(tid))
            cl.addStretch()
            cl.addWidget(unlink_btn)
            cl.addStretch()
            self._lnk_table.setCellWidget(row, 5, cell)

    def _link(self, row: int):
        p = self._potential[row]
        self.db.link_transfer(p["txn_a"]["id"], p["txn_b"]["id"])
        self.refresh()

    def _unlink(self, txn_id: str):
        reply = QMessageBox.question(self, "Unlink Transfer",
            "Unlink this transfer pair? Both transactions will return to normal.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.unlink_transfer(txn_id)
            self.refresh()
