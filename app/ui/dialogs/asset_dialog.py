"""Fixed asset / prepaid add-edit, schedule, disposal and usage dialogs."""
from datetime import date

from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QDoubleSpinBox,
                             QSpinBox, QDialogButtonBox, QVBoxLayout, QLabel,
                             QTextEdit, QMessageBox, QTabWidget, QWidget, QHeaderView)
from PyQt6.QtCore import QDate, Qt

from ui.widgets import DateField, FilterComboBox, DataTable, fmt_date
from sheets.assets_store import ASSET_CLASSES
from utils import depreciation as dep
from utils.money import D, fmt

TAX_NOTE = ("Book depreciation drives your P&L and Balance Sheet. The tax method is "
            "shown for reference only — tax depreciation (Section 179, bonus, MACRS) "
            "has elections and limits; confirm treatment with your accountant.")

# Sensible defaults per class: (life in months, MACRS class)
_DEFAULTS = {"equipment": (84, "7"), "machinery": (84, "7"), "vehicle": (60, "5"),
             "computer": (60, "5"), "furniture": (84, "7"), "software": (36, "3"),
             "improvement": (180, "15"), "prepaid": (12, "5"), "other": (60, "7")}


def _date_iso(field: DateField) -> str:
    return field.date().toString("yyyy-MM-dd")


class AssetDialog(QDialog):
    def __init__(self, db, asset: dict | None = None, parent=None, preset: dict | None = None):
        super().__init__(parent)
        self.db = db
        self.asset = dict(asset or {})
        if not asset and preset:
            self.asset.update(preset)
        self.setWindowTitle("Edit Asset" if asset else "Add Fixed Asset")
        self.setMinimumWidth(560)
        self._build()

    def _money(self, val) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(0, 99_999_999)
        sp.setDecimals(2)
        sp.setPrefix("$")
        sp.setValue(float(val or 0))
        return sp

    def _build(self):
        a = self.asset
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        basics = QWidget()
        f = QFormLayout(basics)
        f.setSpacing(9)
        self.name = QLineEdit(a.get("name", ""))
        self.name.setPlaceholderText("e.g. Laser cutter, 2024 Ford Transit")
        f.addRow("Name *", self.name)
        self.cls = QComboBox()
        for key, label in ASSET_CLASSES:
            self.cls.addItem(label, key)
        self.cls.setCurrentIndex(max(self.cls.findData(a.get("asset_class", "equipment")), 0))
        self.cls.currentIndexChanged.connect(self._class_changed)
        f.addRow("Class", self.cls)
        self.desc = QLineEdit(a.get("description", ""))
        f.addRow("Description", self.desc)
        self.serial = QLineEdit(a.get("serial", ""))
        self.serial.setPlaceholderText("Serial number / asset tag")
        f.addRow("Serial / tag", self.serial)
        self.vendor = QLineEdit(a.get("vendor", ""))
        f.addRow("Vendor", self.vendor)
        self.purchase = DateField()
        self.purchase.setDate(QDate.fromString(a.get("purchase_date") or date.today().isoformat(),
                                               "yyyy-MM-dd"))
        f.addRow("Purchase date", self.purchase)
        self.in_service = DateField()
        self.in_service.setDate(QDate.fromString(
            a.get("in_service_date") or a.get("purchase_date") or date.today().isoformat(),
            "yyyy-MM-dd"))
        f.addRow("In service since", self.in_service)
        self.cost = self._money(a.get("cost"))
        f.addRow("Cost basis *", self.cost)
        self.salvage = self._money(a.get("salvage"))
        f.addRow("Salvage value", self.salvage)
        self.cat = FilterComboBox(placeholder="Depreciation expense category")
        cats = self.db.get_categories_for_book(keep_ids=[a.get("expense_category_id", "")])
        for c in cats:
            if c.get("type") == "expense":
                parent = next((p for p in cats if p["id"] == c.get("parent_id")), None)
                self.cat.add_option(f"{parent['name']} → {c['name']}" if parent else c["name"],
                                    c["id"])
        default_cat = a.get("expense_category_id") or next(
            (c["id"] for c in self.db.get_categories() if c["name"] == "Depreciation"), "")
        self.cat.select_by_data(default_cat)
        f.addRow("Posts to", self.cat)
        tabs.addTab(basics, "Asset")

        book = QWidget()
        b = QFormLayout(book)
        b.setSpacing(9)
        self.method = QComboBox()
        for key, label in dep.METHODS.items():
            self.method.addItem(label, key)
        self.method.setCurrentIndex(max(self.method.findData(a.get("method", "sl")), 0))
        self.method.currentIndexChanged.connect(self._method_changed)
        b.addRow("Book method", self.method)
        self.life = QSpinBox()
        self.life.setRange(1, 600)
        self.life.setSuffix(" months")
        self.life.setValue(int(float(a.get("life_months") or 60)))
        b.addRow("Useful life", self.life)
        self.factor = QDoubleSpinBox()
        self.factor.setRange(1.0, 3.0)
        self.factor.setSingleStep(0.5)
        self.factor.setValue(float(a.get("db_factor") or 2))
        self.factor.setSuffix(" × straight-line rate")
        b.addRow("Declining factor", self.factor)
        self.units = QDoubleSpinBox()
        self.units.setRange(0, 1e9)
        self.units.setDecimals(0)
        self.units.setValue(float(a.get("total_units") or 0))
        self.units.setSuffix(" hours / units over its life")
        b.addRow("Total units", self.units)
        self.macrs = QComboBox()
        for k, label in dep.MACRS_EXAMPLES.items():
            self.macrs.addItem(label, k)
        self.macrs.setCurrentIndex(max(self.macrs.findData(str(a.get("macrs_class") or "5")), 0))
        b.addRow("MACRS class", self.macrs)
        sep = QLabel("Tax reference")
        sep.setStyleSheet("font-weight:700; margin-top:8px;")
        b.addRow(sep)
        self.tax_method = QComboBox()
        for key in ("macrs", "expense", "sl"):
            self.tax_method.addItem(dep.METHODS[key], key)
        self.tax_method.setCurrentIndex(max(self.tax_method.findData(a.get("tax_method", "macrs")), 0))
        b.addRow("Tax method", self.tax_method)
        self.tax_macrs = QComboBox()
        for k, label in dep.MACRS_EXAMPLES.items():
            self.tax_macrs.addItem(label, k)
        self.tax_macrs.setCurrentIndex(max(self.tax_macrs.findData(str(a.get("tax_macrs_class") or "5")), 0))
        b.addRow("Tax MACRS class", self.tax_macrs)
        note = QLabel(TAX_NOTE)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        b.addRow(note)
        tabs.addTab(book, "Depreciation")

        self.notes = QTextEdit(a.get("notes", ""))
        self.notes.setMaximumHeight(70)
        lay.addWidget(QLabel("Notes"))
        lay.addWidget(self.notes)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        if not self.asset.get("id") and not self.asset.get("life_months"):
            self._class_changed()
        self._method_changed()

    def _class_changed(self, *_):
        life, macrs = _DEFAULTS.get(self.cls.currentData(), (60, "7"))
        self.life.setValue(life)
        self.macrs.setCurrentIndex(max(self.macrs.findData(macrs), 0))
        self.tax_macrs.setCurrentIndex(max(self.tax_macrs.findData(macrs), 0))
        if self.cls.currentData() == "prepaid":
            self.method.setCurrentIndex(self.method.findData("sl"))
            self.tax_method.setCurrentIndex(self.tax_method.findData("sl"))
            # Amortization belongs to the expense it prepays (e.g. Insurance).
            dep_cat = next((c["id"] for c in self.db.get_categories()
                            if c["name"] == "Depreciation"), "")
            if self.cat.current_data() == dep_cat:
                self.cat.select_by_data("")

    def _method_changed(self, *_):
        m = self.method.currentData()
        for w, show in ((self.life, m in ("sl", "db")), (self.factor, m == "db"),
                        (self.units, m == "units"), (self.macrs, m == "macrs")):
            lbl = w.parentWidget().layout().labelForField(w)
            w.setVisible(show)
            if lbl:
                lbl.setVisible(show)

    def _accept(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        if self.cost.value() <= 0:
            QMessageBox.warning(self, "Cost Needed", "Enter what the asset cost.")
            return
        if self.salvage.value() >= self.cost.value() and self.method.currentData() != "expense":
            QMessageBox.warning(self, "Salvage Too High", "Salvage must be less than cost.")
            return
        if self.method.currentData() == "units" and self.units.value() <= 0:
            QMessageBox.warning(self, "Units Needed", "Enter the total hours/units expected.")
            return
        self.accept()

    def get_data(self) -> dict:
        d = dict(self.asset)
        d.update({
            "name": self.name.text().strip(), "asset_class": self.cls.currentData(),
            "description": self.desc.text().strip(), "serial": self.serial.text().strip(),
            "vendor": self.vendor.text().strip(), "purchase_date": _date_iso(self.purchase),
            "in_service_date": _date_iso(self.in_service), "cost": self.cost.value(),
            "salvage": self.salvage.value(), "life_months": self.life.value(),
            "method": self.method.currentData(), "db_factor": self.factor.value(),
            "total_units": self.units.value(), "macrs_class": self.macrs.currentData(),
            "tax_method": self.tax_method.currentData(),
            "tax_macrs_class": self.tax_macrs.currentData(),
            "expense_category_id": self.cat.current_data() or "",
            "notes": self.notes.toPlainText().strip(),
        })
        d.setdefault("status", "active")
        return d


class ScheduleDialog(QDialog):
    """Book and tax schedules side by side, plus usage for units-of-production."""
    def __init__(self, db, asset: dict, parent=None, on_usage=None):
        super().__init__(parent)
        self.db = db
        self.asset = asset
        self._on_usage = on_usage
        self.setWindowTitle(f"Depreciation Schedule — {asset['name']}")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        usage = db.get_asset_usage(asset["id"])
        today = date.today().isoformat()
        head = QLabel(
            f"<b>{asset['name']}</b> · cost {fmt(asset.get('cost'))} · in service "
            f"{fmt_date(asset.get('in_service_date', ''))} · book: "
            f"{dep.METHODS.get(asset.get('method'), '')}<br>"
            f"Accumulated to date {fmt(dep.accumulated(asset, today, usage))} · "
            f"net book value {fmt(dep.net_book_value(asset, today, usage))}")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        if asset.get("status") in ("sold", "disposed"):
            g = dep.gain_on_disposal(asset, usage)
            lbl = QLabel(f"{asset['status'].title()} {fmt_date(asset.get('disposal_date', ''))} "
                         f"for {fmt(asset.get('disposal_amount'))} — "
                         f"{'gain' if g >= 0 else 'loss'} of {fmt(abs(g))} (book).")
            lbl.setObjectName("InfoBox")
            lay.addWidget(lbl)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        tabs.addTab(self._table(dep.annual_schedule(asset, usage)), "Book schedule")
        tax_sched = dep.annual_schedule(asset, usage, method=asset.get("tax_method") or "macrs",
                                        macrs_class=asset.get("tax_macrs_class") or "5")
        tax_tab = QWidget()
        tl = QVBoxLayout(tax_tab)
        tl.addWidget(self._table(tax_sched))
        note = QLabel(TAX_NOTE + " Mid-quarter convention, ADS and real property are not "
                      "calculated.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        tl.addWidget(note)
        tabs.addTab(tax_tab, "Tax schedule (reference)")
        if asset.get("method") == "units":
            tabs.addTab(self._usage_tab(usage), "Usage log")
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        if asset.get("method") == "units" and asset.get("status") == "active" and on_usage:
            ub = btns.addButton("Record Usage…", QDialogButtonBox.ButtonRole.ActionRole)
            ub.clicked.connect(self._record_usage)
        lay.addWidget(btns)

    def _record_usage(self):
        if self._on_usage(self.asset):
            self.accept()

    def _table(self, rows) -> DataTable:
        t = DataTable(["Year", "Depreciation", "Accumulated", "Book value"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.setRowCount(len(rows))
        for r, y in enumerate(rows):
            t.set_item(r, 0, str(y["year"]))
            for c, k in ((1, "depreciation"), (2, "accumulated"), (3, "book_value")):
                t.set_item(r, c, fmt(y[k]), align=Qt.AlignmentFlag.AlignRight |
                           Qt.AlignmentFlag.AlignVCenter)
        return t

    def _usage_tab(self, usage) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        t = DataTable(["Date", "Units", "Notes"])
        t.setRowCount(len(usage))
        for r, u in enumerate(usage):
            t.set_item(r, 0, fmt_date(u["date"]))
            t.set_item(r, 1, f"{u['units']:g}")
            t.set_item(r, 2, u.get("notes", ""))
        l.addWidget(t)
        return w


class UsageDialog(QDialog):
    def __init__(self, asset: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Record Usage — {asset['name']}")
        f = QFormLayout(self)
        self.date = DateField()
        f.addRow("Date", self.date)
        self.units = QDoubleSpinBox()
        self.units.setRange(0, 1e7)
        self.units.setDecimals(1)
        self.units.setSuffix(" hours / units")
        f.addRow("Used", self.units)
        self.notes = QLineEdit()
        f.addRow("Notes", self.notes)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        f.addRow(btns)


class DisposeDialog(QDialog):
    def __init__(self, db, asset: dict, parent=None):
        super().__init__(parent)
        self.db = db
        self.asset = asset
        self._usage = db.get_asset_usage(asset["id"])
        self.setWindowTitle(f"Sell or Dispose — {asset['name']}")
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        self.sold = QComboBox()
        self.sold.addItem("Sold", True)
        self.sold.addItem("Disposed / scrapped", False)
        f.addRow("What happened", self.sold)
        self.date = DateField()
        self.date.dateChanged.connect(self._update)
        f.addRow("Date", self.date)
        self.proceeds = QDoubleSpinBox()
        self.proceeds.setRange(0, 99_999_999)
        self.proceeds.setDecimals(2)
        self.proceeds.setPrefix("$")
        self.proceeds.valueChanged.connect(self._update)
        f.addRow("Sale price", self.proceeds)
        self.acct = QComboBox()
        self.acct.addItem("— not received into an account —", "")
        for a in db.get_accounts():
            if a.get("type") != "equity":
                self.acct.addItem(a["name"], a["id"])
        f.addRow("Deposited to", self.acct)
        self.result = QLabel("")
        self.result.setObjectName("InfoBox")
        self.result.setWordWrap(True)
        f.addRow(self.result)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        f.addRow(btns)
        self._update()

    def _update(self, *_):
        d = _date_iso(self.date)
        probe = dict(self.asset, status="sold", disposal_date=d,
                     disposal_amount=self.proceeds.value())
        nbv = D(self.asset.get("cost")) - dep.accumulated(probe, d, self._usage)
        g = dep.gain_on_disposal(probe, self._usage)
        self.result.setText(f"Book value on that date: {fmt(nbv)}.  "
                            f"{'Gain' if g >= 0 else 'Loss'} on disposal: {fmt(abs(g))} — "
                            "shown on the P&L (not a cash transaction).")

    def values(self) -> dict:
        return {"date": _date_iso(self.date), "proceeds": self.proceeds.value(),
                "account_id": self.acct.currentData() or "", "sold": self.sold.currentData()}
