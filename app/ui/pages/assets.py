"""Fixed assets & prepaid expenses — cost, depreciation, book value."""
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QMessageBox, QHeaderView, QCheckBox)
from PyQt6.QtCore import Qt

from ui.widgets import PageTitle, DataTable, SecondaryButton, fmt_date
from ui.styles import MUTED, SUCCESS, WARNING
from sheets.assets_store import ASSET_CLASSES
from utils import depreciation as dep
from utils.money import fmt, money_sum

ROW_H = 44
_CLASS = dict(ASSET_CLASSES)


class AssetsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._assets: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Assets"))
        hdr.addStretch()
        self._show_old = QCheckBox("Show sold / disposed")
        self._show_old.toggled.connect(self.refresh)
        hdr.addWidget(self._show_old)
        self._add_btn = QPushButton("+ Add Asset")
        self._add_btn.clicked.connect(lambda: self.add_asset())
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        info = QLabel(
            "Machines, equipment, vehicles and prepaid expenses (an annual insurance "
            "policy, a software license). Their cost goes on the <b>Balance Sheet</b> and is "
            "spread over their life as <b>depreciation</b> on the P&amp;L — calculated "
            "automatically, no bank entries needed. Record the purchase itself under the "
            "<i>Fixed Assets</i> or <i>Prepaid Expenses</i> category so it isn't expensed twice.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setObjectName("InfoBox")
        lay.addWidget(info)

        self._table = DataTable(["Asset", "In service", "Cost", "Accum. dep.",
                                 "Book value", "Actions"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 92), (2, 96), (3, 96), (4, 100)):
            self._table.setColumnWidth(c, w)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 232)
        hh.setStretchLastSection(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.doubleClicked.connect(lambda i: self._schedule(self._assets[i.row()]))
        lay.addWidget(self._table, 1)
        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        lay.addWidget(self._summary)

    def refresh(self):
        today = date.today()
        iso = today.isoformat()
        month_start = today.replace(day=1).isoformat()
        self._assets = self.db.get_assets(include_disposed=self._show_old.isChecked())
        usage = self.db.usage_by_asset()
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._assets))
        tot_cost, tot_acc, tot_nbv, tot_month = [], [], [], []
        for r, a in enumerate(self._assets):
            u = usage.get(a["id"], [])
            acc = dep.accumulated(a, iso, u)
            nbv = dep.net_book_value(a, iso, u)
            this_month = dep.depreciation_between(a, month_start, iso, u)
            name = t.set_item(r, 0, a["name"], bold=True)
            meth = {"sl": "Straight-line", "db": "Declining balance", "units": "Units",
                    "expense": "§179 / bonus", "macrs": "MACRS"}.get(a.get("method"), "")
            name.setToolTip(f"{_CLASS.get(a.get('asset_class'), '')}  ·  {meth}  ·  "
                            f"depreciation this month {fmt(this_month)}")
            t.set_item(r, 1, fmt_date(a.get("in_service_date", "")))
            right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            t.set_item(r, 2, fmt(a.get("cost")), align=right)
            t.set_item(r, 3, fmt(acc), align=right)
            if a.get("status") == "active":
                t.set_item(r, 4, fmt(nbv), align=right, bold=True)
                tot_cost.append(a.get("cost"))
                tot_acc.append(acc)
                tot_nbv.append(nbv)
                tot_month.append(this_month)
            else:
                t.set_item(r, 4, a["status"].title(), align=right, color=WARNING)
            t.setCellWidget(r, 5, self._actions(a))
            t.setRowHeight(r, ROW_H)
        self._summary.setText(
            f"{len(tot_cost)} active  ·  cost {fmt(money_sum(tot_cost))}  ·  accumulated "
            f"{fmt(money_sum(tot_acc))}  ·  book value {fmt(money_sum(tot_nbv))}  ·  "
            f"depreciation this month {fmt(money_sum(tot_month))}  ·  double-click for the "
            "schedule")

    def _actions(self, a) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        for label, name, slot, width in (("Schedule", "CellButton", self._schedule, 82),
                                         ("Edit", "CellButton", self._edit, 52)):
            b = QPushButton(label)
            b.setObjectName(name)
            b.setMinimumWidth(width)
            b.clicked.connect(lambda _, x=a, s=slot: s(x))
            lay.addWidget(b)
        if a.get("status") == "active":
            b = QPushButton("Sell…")
            b.setObjectName("CellDanger")
            b.setMinimumWidth(58)
            b.clicked.connect(lambda _, x=a: self._dispose(x))
            lay.addWidget(b)
        else:
            b = QPushButton("Delete")
            b.setObjectName("CellDanger")
            b.clicked.connect(lambda _, x=a: self._delete(x))
            lay.addWidget(b)
        return cell

    # ── actions ──────────────────────────────────────────────────────────────

    def add_asset(self, preset: dict | None = None) -> dict | None:
        from ui.dialogs.asset_dialog import AssetDialog
        dlg = AssetDialog(self.db, parent=self, preset=preset)
        if dlg.exec() != AssetDialog.DialogCode.Accepted:
            return None
        saved = self.db.save_asset(dlg.get_data())
        self.refresh()
        return saved

    def _edit(self, a):
        from ui.dialogs.asset_dialog import AssetDialog
        dlg = AssetDialog(self.db, asset=a, parent=self)
        if dlg.exec() == AssetDialog.DialogCode.Accepted:
            self.db.save_asset(dlg.get_data())
            self.refresh()

    def _schedule(self, a):
        from ui.dialogs.asset_dialog import ScheduleDialog
        ScheduleDialog(self.db, a, parent=self, on_usage=self._usage).exec()
        self.refresh()

    def _usage(self, a) -> bool:
        from ui.dialogs.asset_dialog import UsageDialog
        dlg = UsageDialog(a, parent=self)
        if dlg.exec():
            self.db.add_asset_usage(a["id"], dlg.date.date().toString("yyyy-MM-dd"),
                                    dlg.units.value(), dlg.notes.text().strip())
            return True
        return False

    def _dispose(self, a):
        from ui.dialogs.asset_dialog import DisposeDialog
        dlg = DisposeDialog(self.db, a, parent=self)
        if dlg.exec():
            v = dlg.values()
            self.db.dispose_asset(a["id"], v["date"], v["proceeds"], v["account_id"], v["sold"])
            self.refresh()

    def _delete(self, a):
        if QMessageBox.question(
                self, "Delete Asset",
                f"Delete “{a['name']}” from the asset register? Its past depreciation and "
                "gain/loss will disappear from reports. Transactions are not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.db.delete_asset(a["id"])
            self.refresh()
