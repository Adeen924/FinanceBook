"""Inventory — items, stock on hand, movements, counts and valuation."""
import csv
from datetime import date

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QHeaderView, QTabWidget, QDialog, QFormLayout, QLineEdit,
                             QComboBox, QDoubleSpinBox, QDialogButtonBox, QMessageBox,
                             QFileDialog, QCheckBox)
from PyQt6.QtCore import Qt, QDate

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, FilterComboBox, fmt_date
from ui.styles import WARNING, DANGER, MUTED
from sheets.inventory_store import MOVE_KINDS, ITEM_KINDS
from utils.inventory import METHODS
from utils.money import fmt, money_sum

ROW_H = 44


def _spin(decimals=2, prefix="", maximum=9_999_999) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, maximum)
    s.setDecimals(decimals)
    s.setPrefix(prefix)
    return s


class ItemDialog(QDialog):
    def __init__(self, db, item: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.item = dict(item or {})
        self.setWindowTitle("Edit Item" if item else "Add Item")
        self.setMinimumWidth(480)
        it = self.item
        f = QFormLayout(self)
        f.setSpacing(9)
        self.name = QLineEdit(it.get("name", ""))
        f.addRow("Name *", self.name)
        self.sku = QLineEdit(it.get("sku", ""))
        f.addRow("SKU", self.sku)
        self.desc = QLineEdit(it.get("description", ""))
        f.addRow("Description", self.desc)
        self.kind = QComboBox()
        for k, label in ITEM_KINDS:
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(self.kind.findData(it.get("kind", "finished")), 0))
        f.addRow("Type", self.kind)
        self.unit = QLineEdit(it.get("unit", "each"))
        f.addRow("Unit", self.unit)
        self.price = _spin(prefix="$")
        self.price.setValue(float(it.get("sale_price") or 0))
        f.addRow("Sale price", self.price)
        self.reorder = _spin(decimals=0, maximum=1e9)
        self.reorder.setValue(float(it.get("reorder_point") or 0))
        f.addRow("Reorder at", self.reorder)
        self.method = QComboBox()
        for k, label in METHODS.items():
            self.method.addItem(label, k)
        self.method.setCurrentIndex(max(self.method.findData(it.get("cost_method", "fifo")), 0))
        f.addRow("Costing", self.method)
        self.vendor = FilterComboBox(placeholder="Default vendor (optional)")
        for c in db.get_contacts("vendor"):
            self.vendor.add_option(c["name"], c["id"])
        self.vendor.select_by_data(it.get("default_vendor_id", ""))
        f.addRow("Vendor", self.vendor)
        self.cat = FilterComboBox(placeholder="Income category for sales")
        for c in db.get_categories():
            if c.get("type") == "income":
                self.cat.add_option(c["name"], c["id"])
        self.cat.select_by_data(it.get("income_category_id", ""))
        f.addRow("Sales category", self.cat)
        self.tracked = QCheckBox("Track stock and cost of goods sold")
        self.tracked.setChecked(str(it.get("tracked", "1")) != "0")
        f.addRow("", self.tracked)
        if it.get("id") and db.get_moves(it["id"]):
            note = QLabel("Changing the costing method recalculates past COGS for this item.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            f.addRow(note)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        f.addRow(btns)

    def _ok(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        d = dict(self.item)
        d.update({"name": self.name.text().strip(), "sku": self.sku.text().strip(),
                  "description": self.desc.text().strip(), "kind": self.kind.currentData(),
                  "unit": self.unit.text().strip() or "each", "sale_price": self.price.value(),
                  "reorder_point": self.reorder.value(), "cost_method": self.method.currentData(),
                  "default_vendor_id": self.vendor.current_data() or "",
                  "income_category_id": self.cat.current_data() or "",
                  "tracked": "1" if self.tracked.isChecked() else "0"})
        d.setdefault("active", "1")
        return d


class StockDialog(QDialog):
    """Receive, sell, count or return stock for one item."""
    TITLES = {"purchase": "Receive Stock", "sale": "Record Sale", "count": "Stock Count",
              "return_in": "Customer Return", "return_out": "Return to Vendor",
              "opening": "Opening Stock"}

    def __init__(self, db, mode: str, item: dict | None = None, parent=None, preset=None):
        super().__init__(parent)
        self.db = db
        self.mode = mode
        preset = preset or {}
        self.setWindowTitle(self.TITLES[mode])
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        f.setSpacing(9)
        self.item = FilterComboBox(placeholder="Item")
        for it in db.get_items():
            self.item.add_option(f"{it['name']}" + (f"  ({it['sku']})" if it.get("sku") else ""),
                                 it["id"])
        self.item.select_by_data((item or {}).get("id", preset.get("item_id", "")))
        self.item.currentIndexChanged.connect(self._item_changed)
        f.addRow("Item *", self.item)
        self.date = DateField()
        if preset.get("date"):
            self.date.setDate(QDate.fromString(preset["date"], "yyyy-MM-dd"))
        f.addRow("Date", self.date)
        self.qty = _spin(decimals=2, maximum=1e9)
        self.qty.setValue(float(preset.get("qty") or (0 if mode == "count" else 1)))
        f.addRow("Counted quantity" if mode == "count" else "Quantity", self.qty)
        self.on_hand = QLabel("")
        self.on_hand.setObjectName("Muted")
        f.addRow("", self.on_hand)
        self.cost = _spin(decimals=4, prefix="$")
        self.cost.setValue(float(preset.get("unit_cost") or 0))
        self.price = _spin(prefix="$")
        if mode in ("purchase", "opening", "return_out"):
            f.addRow("Unit cost", self.cost)
        if mode == "sale":
            f.addRow("Unit price", self.price)
        self.acct = QComboBox()
        self.acct.addItem("— don't record money (already entered) —", "")
        for a in db.get_accounts():
            if a.get("type") != "equity" or a.get("role") in ("owner_capital", "owner_paid"):
                self.acct.addItem(a["name"], a["id"])
        if mode in ("purchase", "sale"):
            f.addRow("Paid from" if mode == "purchase" else "Deposited to", self.acct)
            hint = QLabel("Pick an account to also record the money; leave it blank if the "
                          "payment is already in your transactions (e.g. imported or from a receipt)."
                          if mode == "purchase" else
                          "Pick an account to record the income; leave blank if the sale was "
                          "invoiced or already recorded.")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            f.addRow(hint)
        self.notes = QLineEdit(preset.get("notes", ""))
        f.addRow("Notes", self.notes)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        f.addRow(btns)
        self._item_changed()

    def _item_changed(self, *_):
        it = self.db.get_item(self.item.current_data() or "")
        if not it:
            self.on_hand.setText("")
            return
        st = self.db.item_status(it)
        self.on_hand.setText(f"On hand: {float(st['qty']):g} {it.get('unit', '')} · "
                             f"unit cost {fmt(st['unit_cost'])}")
        if self.mode == "sale" and not self.price.value():
            self.price.setValue(float(it.get("sale_price") or 0))
        if self.mode in ("purchase", "return_out") and not self.cost.value():
            self.cost.setValue(float(st["unit_cost"]))

    def _ok(self):
        iid = self.item.current_data()
        if not iid:
            QMessageBox.warning(self, "Item", "Choose an item (or add one first).")
            return
        if self.mode != "count" and self.qty.value() <= 0:
            self.qty.setFocus()
            return
        d = self.date.date().toString("yyyy-MM-dd")
        q, notes = self.qty.value(), self.notes.text().strip()
        if self.mode == "purchase":
            acct = self.db.get_account(self.acct.currentData() or "") or {}
            paid_by = self.db.owner_person_id() if acct.get("role") in (
                "owner_capital", "due_to_owner", "owner_paid") else ""
            self.db.receive_stock(iid, d, q, self.cost.value(), self.acct.currentData() or "",
                                  notes=notes, paid_by=paid_by)
        elif self.mode == "sale":
            st = self.db.item_status(self.db.get_item(iid), d)
            if q > float(st["qty"]) and QMessageBox.question(
                    self, "Not Enough Stock",
                    f"Only {float(st['qty']):g} on hand on that date. Record the sale anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            self.db.sell_stock(iid, d, q, self.price.value(), self.acct.currentData() or "", notes)
        elif self.mode == "count":
            if self.db.count_stock(iid, d, q, notes) is None:
                QMessageBox.information(self, "No Change", "The count matches what's on record.")
        else:
            self.db.add_move(iid, d, self.mode, q, unit_cost=self.cost.value(), notes=notes)
        self.accept()


class InventoryPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._items: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Inventory"))
        hdr.addStretch()
        for label, mode in (("Count", "count"), ("Sell", "sale")):
            b = SecondaryButton(label + "…")
            b.clicked.connect(lambda _, m=mode: self._stock(m))
            hdr.addWidget(b)
        recv = SecondaryButton("Receive…")
        recv.clicked.connect(lambda: self._stock("purchase"))
        hdr.addWidget(recv)
        self._add_btn = QPushButton("+ Item")
        self._add_btn.clicked.connect(self._add_item)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        self._summary = QLabel("")
        self._summary.setObjectName("InfoBox")
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        self._summary.setWordWrap(True)
        lay.addWidget(self._summary)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)
        self._table = DataTable(["Item", "SKU", "On hand", "Unit cost", "Value", "Status", "Actions"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 96), (2, 84), (3, 92), (4, 100), (5, 92)):
            self._table.setColumnWidth(c, w)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(6, 176)
        hh.setStretchLastSection(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.doubleClicked.connect(lambda i: self._show_moves(self._items[i.row()]))
        self._tabs.addTab(self._page(self._table), "Items")

        hist = QWidget()
        hl = QVBoxLayout(hist)
        hl.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self._hist_item = QComboBox()
        self._hist_item.currentIndexChanged.connect(self._fill_moves)
        row.addWidget(self._hist_item, 1)
        del_btn = SecondaryButton("Delete Selected Move")
        del_btn.clicked.connect(self._delete_move)
        row.addWidget(del_btn)
        hl.addLayout(row)
        self._moves = DataTable(["Date", "Item", "Movement", "Qty", "Unit cost / price",
                                 "Cost", "On hand after", "Notes"])
        self._moves.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 104), (1, 140), (2, 130), (3, 64), (4, 120), (5, 96), (6, 104)):
            self._moves.setColumnWidth(c, w)
        hl.addWidget(self._moves)
        self._tabs.addTab(hist, "Movements")

        val = QWidget()
        vl = QVBoxLayout(val)
        vl.setContentsMargins(0, 8, 0, 0)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("Valuation as of"))
        self._val_date = DateField()
        vrow.addWidget(self._val_date)
        run = QPushButton("Run")
        run.clicked.connect(self._fill_valuation)
        vrow.addWidget(run)
        exp = SecondaryButton("Export CSV")
        exp.clicked.connect(self._export_valuation)
        vrow.addWidget(exp)
        vrow.addStretch()
        vl.addLayout(vrow)
        self._val = DataTable(["Item", "SKU", "Method", "Qty", "Unit cost", "Value"])
        self._val.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 100), (2, 150), (3, 80), (4, 100), (5, 110)):
            self._val.setColumnWidth(c, w)
        vl.addWidget(self._val)
        self._tabs.addTab(val, "Valuation")

    def _page(self, w) -> QWidget:
        p = QWidget()
        l = QVBoxLayout(p)
        l.setContentsMargins(0, 8, 0, 0)
        l.addWidget(w)
        return p

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self):
        self._items = self.db.get_items()
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._items))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, it in enumerate(self._items):
            st = self.db.item_status(it)
            t.set_item(r, 0, it["name"], bold=True)
            t.set_item(r, 1, it.get("sku") or "—", color=MUTED)
            t.set_item(r, 2, f"{float(st['qty']):g}", align=right, bold=True,
                       color=DANGER if st["qty"] < 0 else None)
            t.set_item(r, 3, fmt(st["unit_cost"]), align=right)
            t.set_item(r, 4, fmt(st["value"]), align=right)
            status = "Oversold" if st["qty"] < 0 else "Reorder" if st["low"] else "OK"
            t.set_item(r, 5, status, bold=status != "OK",
                       color=DANGER if status == "Oversold" else WARNING if status == "Reorder" else MUTED)
            t.setCellWidget(r, 6, self._actions(it))
            t.setRowHeight(r, ROW_H)
        tot = self.db.inventory_totals(date.today().isoformat(), f"{date.today().year}-01-01")
        low = tot["low_stock"]
        self._summary.setText(
            f"<b>Inventory value:</b> {fmt(tot['value'])} &nbsp;·&nbsp; <b>Cost of goods sold this "
            f"year:</b> {fmt(tot['cogs'])}" + (f" &nbsp;·&nbsp; <b>Reorder:</b> {', '.join(low)}"
                                               if low else "") +
            "<br><span style='font-size:11px;'>Record stock purchases under <i>Inventory "
            "Purchases</i> (not an expense) — cost reaches the P&amp;L as COGS when items sell.</span>")
        cur = self._hist_item.currentData()
        self._hist_item.blockSignals(True)
        self._hist_item.clear()
        self._hist_item.addItem("All items", "")
        for it in self._items:
            self._hist_item.addItem(it["name"], it["id"])
        self._hist_item.setCurrentIndex(max(self._hist_item.findData(cur), 0))
        self._hist_item.blockSignals(False)
        self._fill_moves()
        self._fill_valuation()

    def _actions(self, it) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        e = QPushButton("Edit")
        e.setObjectName("CellButton")
        e.clicked.connect(lambda _, x=it: self._edit_item(x))
        lay.addWidget(e)
        h = QPushButton("History")
        h.setObjectName("CellButton")
        h.setMinimumWidth(70)
        h.clicked.connect(lambda _, x=it: self._show_moves(x))
        lay.addWidget(h)
        return cell

    def _fill_moves(self, *_):
        from utils.inventory import run_costing
        iid = self._hist_item.currentData() or ""
        items = {i["id"]: i for i in self.db.get_items(active_only=False)}
        rows = []
        for it_id in ([iid] if iid else list(items)):
            it = items.get(it_id)
            if not it:
                continue
            res = run_costing(self.db.get_moves(it_id), it.get("cost_method") or "fifo")
            rows += [dict(m, item_name=it["name"]) for m in res["costed"]]
        rows.sort(key=lambda m: (m.get("date", ""), m.get("created_at", "")), reverse=True)
        self._move_rows = rows
        t = self._moves
        t.clear_rows()
        t.setRowCount(len(rows))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, m in enumerate(rows):
            t.set_item(r, 0, fmt_date(m.get("date", "")))
            t.set_item(r, 1, m["item_name"])
            t.set_item(r, 2, MOVE_KINDS.get(m.get("kind"), m.get("kind", "")))
            q = float(m.get("qty") or 0)
            t.set_item(r, 3, f"{q:+g}", align=right, color=DANGER if q < 0 else None)
            per = m.get("unit_price") if m.get("kind") == "sale" else m.get("unit_cost")
            t.set_item(r, 4, fmt(per or 0), align=right)
            t.set_item(r, 5, fmt(m["cost"]), align=right)
            t.set_item(r, 6, f"{float(m['qty_after']):g}", align=right)
            t.set_item(r, 7, m.get("notes", ""), color=MUTED)

    def _fill_valuation(self):
        from utils.inventory import run_costing
        as_of = self._val_date.date().toString("yyyy-MM-dd")
        rows = []
        for it in self.db.get_items(active_only=False):
            res = run_costing(self.db.get_moves(it["id"]), it.get("cost_method") or "fifo", as_of)
            if res["qty"] or res["value"]:
                rows.append((it, res))
        t = self._val
        t.clear_rows()
        t.setRowCount(len(rows) + 1)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, (it, res) in enumerate(rows):
            t.set_item(r, 0, it["name"])
            t.set_item(r, 1, it.get("sku", ""))
            t.set_item(r, 2, METHODS.get(it.get("cost_method") or "fifo", ""))
            t.set_item(r, 3, f"{float(res['qty']):g}", align=right)
            t.set_item(r, 4, fmt(res["unit_cost"]), align=right)
            t.set_item(r, 5, fmt(res["value"]), align=right, bold=True)
        total = money_sum(res["value"] for _, res in rows)
        t.set_item(len(rows), 0, "Total", bold=True)
        t.set_item(len(rows), 5, fmt(total), align=right, bold=True)
        self._val_rows = rows

    # ── actions ──────────────────────────────────────────────────────────────

    def _add_item(self):
        dlg = ItemDialog(self.db, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item = self.db.save_item(dlg.get_data())
            if QMessageBox.question(
                    self, "Starting Stock",
                    "Do you already have some of this item on hand? (Recorded as opening stock "
                    "— no purchase transaction.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                StockDialog(self.db, "opening", item, parent=self).exec()
            self.refresh()

    def _edit_item(self, it):
        dlg = ItemDialog(self.db, it, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_item(dlg.get_data())
            self.refresh()

    def _stock(self, mode, item=None):
        if not self.db.get_items():
            QMessageBox.information(self, "No Items", "Add an item first.")
            return
        if StockDialog(self.db, mode, item, parent=self).exec():
            self.refresh()

    def _show_moves(self, it):
        self._tabs.setCurrentIndex(1)
        self._hist_item.setCurrentIndex(max(self._hist_item.findData(it["id"]), 0))

    def _delete_move(self):
        row = self._moves.currentRow()
        rows = getattr(self, "_move_rows", [])
        if not 0 <= row < len(rows):
            return
        m = rows[row]
        msg = "Delete this stock movement?"
        if m.get("txn_id"):
            msg += "\n\nThe transaction it created stays in your books — delete it there if needed."
        if QMessageBox.question(self, "Delete Movement", msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.db.delete_move(m["id"])
            self.refresh()

    def _export_valuation(self):
        as_of = self._val_date.date().toString("yyyy-MM-dd")
        path, _ = QFileDialog.getSaveFileName(self, "Export Valuation",
                                              f"inventory_valuation_{as_of}.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Item", "SKU", "Method", "Quantity", "Unit cost", "Value"])
            for it, res in self._val_rows:
                w.writerow([it["name"], it.get("sku", ""), it.get("cost_method", "fifo"),
                            f"{res['qty']}", f"{res['unit_cost']:.2f}", f"{res['value']:.2f}"])
            w.writerow(["Total", "", "", "", "", f"{money_sum(r['value'] for _, r in self._val_rows):.2f}"])
