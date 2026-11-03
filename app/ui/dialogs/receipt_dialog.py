"""Receipt review: the receipt beside an editable table of its line items."""
import os

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
                             QLineEdit, QComboBox, QDoubleSpinBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
                             QScrollArea, QTextEdit, QWidget, QCheckBox, QMessageBox,
                             QAbstractItemView, QFrame)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QPixmap, QDesktopServices

from ui.widgets import DateField, FilterComboBox, SecondaryButton
from ui.styles import WARNING, SUCCESS, MUTED
from utils.money import D, cents, money_sum, ZERO
from parsers import receipt as R

COL_DESC, COL_QTY, COL_UNIT, COL_TOTAL, COL_CAT, COL_CLASS, COL_CHARGED, COL_BIZ, COL_KIND = range(9)
KINDS = [("expense", "Expense")]   # later phases add Fixed asset / Inventory


class _AIWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, path, key, model):
        super().__init__()
        self._args = (path, key, model)

    def run(self):
        try:
            self.done.emit(R.parse_with_ai(*self._args))
        except R.AIReceiptError as e:
            self.failed.emit(str(e))
        except Exception as e:      # never let a worker crash the app
            self.failed.emit(f"Couldn't read the receipt ({type(e).__name__}).")


def kinds_for(db) -> list[tuple[str, str]]:
    """Line-item types available in this book."""
    kinds = list(KINDS)
    if db.is_business():
        kinds.append(("asset", "Fixed asset"))
    return kinds


class ReceiptReviewDialog(QDialog):
    def __init__(self, db, receipt: dict, parent=None, parsed: dict | None = None):
        super().__init__(parent)
        self.db = db
        self.receipt = dict(receipt)
        self._worker = None
        self.booked = False
        self.setWindowTitle("Review Receipt")
        self.resize(1240, 760)
        self.setMinimumSize(980, 600)
        self._cats = db.get_categories()
        self._classes = db.get_classes()
        self._people = db.get_people() if db.is_business() else []
        self._base_txn = self._attached_txn()
        self._build()
        items = db.get_receipt_items(receipt["id"])
        if items:
            self._load_header(self.receipt)
            self._load_items(items)
        elif parsed:
            self._apply_parsed(parsed)
        else:
            self._load_header(self.receipt)
            self._load_items([])
        notes = (parsed or {}).get("warnings") or []
        if parsed and parsed.get("method") == "manual" and notes:
            self.parse_note.setText(notes[0])
            self.parse_note.show()
        self._update_check()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split, 1)

        # Left: the receipt itself
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        name = QLabel(self.receipt.get("original_name") or "Manual receipt")
        name.setObjectName("CardTitle")
        name.setWordWrap(True)
        ll.addWidget(name)
        path = self.db.receipt_abs_path(self.receipt)
        ext = os.path.splitext(path)[1].lower()
        if path and ext in R.IMAGE_EXTS and QPixmap(path).isNull() is False:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            img = QLabel()
            img.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            pm = QPixmap(path)
            img.setPixmap(pm.scaledToWidth(min(pm.width(), 520),
                                           Qt.TransformationMode.SmoothTransformation))
            sa.setWidget(img)
            ll.addWidget(sa, 1)
        else:
            txt = QTextEdit()
            txt.setReadOnly(True)
            raw = self.receipt.get("raw_text") or ""
            if not raw and path and os.path.exists(path):
                try:
                    raw = R.extract_text(path)
                except Exception:
                    raw = ""
            txt.setPlainText(raw or ("No text could be read from this file. Open it, then "
                                     "type the details on the right — or use Read with AI."))
            txt.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;")
            ll.addWidget(txt, 1)
        open_btn = SecondaryButton("Open File")
        open_btn.setEnabled(bool(path and os.path.exists(path)))
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        ll.addWidget(open_btn)
        split.addWidget(left)

        # Right: header + items
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        form = QFormLayout()
        form.setSpacing(8)
        self.vendor_edit = QLineEdit()
        self.vendor_edit.setPlaceholderText("Store / vendor")
        form.addRow("Vendor", self.vendor_edit)
        self.date_edit = DateField()
        form.addRow("Date", self.date_edit)

        self.acct_combo = QComboBox()
        for a in self.db.get_accounts():
            self.acct_combo.addItem(a["name"], a["id"])
        default_acct = (self._base_txn or {}).get("account_id") or \
            self.db.get_setting("default_funding_account")
        if not default_acct and self.db.is_business():
            default_acct = (self.db.get_account_by_role(
                "due_to_owner" if self.db.get_setting("owner_paid_default_treatment") == "reimburse"
                else "owner_capital") or {}).get("id", "")
        ix = self.acct_combo.findData(default_acct)
        if ix >= 0:
            self.acct_combo.setCurrentIndex(ix)
        form.addRow("Paid from", self.acct_combo)
        if self._base_txn:
            self.acct_combo.setEnabled(False)
            amt = abs(float(self._base_txn.get("amount") or 0))
            note = QLabel(f"Splits the attached transaction ({self._base_txn.get('date', '')}, "
                          f"${amt:,.2f}) instead of adding a new one.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            form.addRow("", note)
        elif self.db.is_business():
            hint = QLabel("Pick an owner account (Capital / Due to Owner) if you paid personally.")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            form.addRow("", hint)

        money_row = QHBoxLayout()
        self.sub_spin, self.tax_spin, self.tip_spin, self.total_spin = (
            self._money_spin() for _ in range(4))
        for label, sp in (("Subtotal", self.sub_spin), ("Tax", self.tax_spin),
                          ("Tip", self.tip_spin), ("Total", self.total_spin)):
            money_row.addWidget(QLabel(label))
            money_row.addWidget(sp)
            sp.valueChanged.connect(self._update_check)
        form.addRow("Amounts", money_row)

        tax_row = QHBoxLayout()
        self.tax_mode = QComboBox()
        self.tax_mode.addItem("Spread tax & tip over the items", "proportional")
        self.tax_mode.addItem("Book tax & tip as their own lines", "separate")
        self.tax_mode.currentIndexChanged.connect(self._on_tax_mode)
        tax_row.addWidget(self.tax_mode)
        self.tax_cat = self._category_combo("Tax category")
        self.tip_cat = self._category_combo("Tip category")
        tax_row.addWidget(self.tax_cat)
        tax_row.addWidget(self.tip_cat)
        form.addRow("Tax / tip", tax_row)
        rl.addLayout(form)

        self.parse_note = QLabel("")
        self.parse_note.setObjectName("InfoBox")
        self.parse_note.setWordWrap(True)
        self.parse_note.hide()
        rl.addWidget(self.parse_note)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Description", "Qty", "Unit $", "Line $", "Category", "Class / Project",
             "Charged to", "Business", "Type"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
        for col, w in ((COL_QTY, 56), (COL_UNIT, 76), (COL_TOTAL, 84), (COL_CAT, 210),
                       (COL_CLASS, 130), (COL_CHARGED, 130), (COL_BIZ, 70), (COL_KIND, 96)):
            self.table.setColumnWidth(col, w)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        if not self.db.is_business():
            for col in (COL_CHARGED, COL_BIZ):
                self.table.setColumnHidden(col, True)
        rl.addWidget(self.table, 1)

        tools = QHBoxLayout()
        add_btn = SecondaryButton("+ Line")
        add_btn.clicked.connect(lambda: self._add_row({}))
        del_btn = SecondaryButton("Remove Line")
        del_btn.clicked.connect(self._remove_row)
        sug_btn = SecondaryButton("Suggest Categories")
        sug_btn.setToolTip("Fill empty categories from past receipts, this vendor and your Rules")
        sug_btn.clicked.connect(self._suggest_all)
        self.ai_btn = SecondaryButton("Read with AI")
        self.ai_btn.clicked.connect(self._read_with_ai)
        for b in (add_btn, del_btn, sug_btn):
            tools.addWidget(b)
        tools.addStretch()
        tools.addWidget(self.ai_btn)
        rl.addLayout(tools)

        self.check_lbl = QLabel("")
        self.check_lbl.setWordWrap(True)
        rl.addWidget(self.check_lbl)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setSizes([340, 900])

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = SecondaryButton("Cancel")
        cancel.clicked.connect(self.reject)
        draft = SecondaryButton("Save Draft")
        draft.clicked.connect(self._save_draft)
        self.book_btn = QPushButton("Save to Books")
        self.book_btn.clicked.connect(self._book)
        for b in (cancel, draft, self.book_btn):
            btns.addWidget(b)
        lay.addLayout(btns)
        self._on_tax_mode()

    def _money_spin(self) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(-9_999_999, 9_999_999)
        sp.setDecimals(2)
        sp.setPrefix("$")
        sp.setMinimumWidth(96)
        return sp

    def _category_combo(self, placeholder="Category") -> FilterComboBox:
        combo = FilterComboBox(placeholder=placeholder)
        roots = [c for c in self._cats if not c.get("parent_id")]
        for cat in roots:
            if cat.get("type") == "income":
                continue
            combo.add_option(cat["name"], cat["id"])
            for sub in self._cats:
                if sub.get("parent_id") == cat["id"]:
                    combo.add_option(f"{cat['name']} → {sub['name']}", sub["id"])
        return combo

    def _attached_txn(self):
        tid = self.receipt.get("transaction_id") or ""
        if not tid or self.receipt.get("status") == "booked":
            return None
        return self.db.get_transaction(tid)

    # ── loading ──────────────────────────────────────────────────────────────

    def _load_header(self, h: dict):
        self.vendor_edit.setText(h.get("vendor") or "")
        d = QDate.fromString(h.get("receipt_date") or h.get("date") or "", "yyyy-MM-dd")
        if d.isValid():
            self.date_edit.setDate(d)
        elif self._base_txn:
            self.date_edit.setDate(QDate.fromString(self._base_txn.get("date", ""), "yyyy-MM-dd"))
        for sp, k in ((self.sub_spin, "subtotal"), (self.tax_spin, "tax"),
                      (self.tip_spin, "tip"), (self.total_spin, "total")):
            sp.blockSignals(True)
            sp.setValue(float(h.get(k) or 0))
            sp.blockSignals(False)
        if not h.get("total") and self._base_txn:
            self.total_spin.setValue(abs(float(self._base_txn.get("amount") or 0)))

    def _apply_parsed(self, p: dict):
        self._load_header(p)
        vendor = p.get("vendor") or ""
        rows = []
        for it in p.get("line_items") or []:
            rows.append({"description": it["description"], "qty": float(it["qty"]),
                         "unit_price": float(it["unit_price"]),
                         "line_total": float(it["line_total"]),
                         "category_id": self.db.suggest_category(vendor, it["description"])})
        if not rows and (p.get("total") or p.get("subtotal")):
            # No itemisation — one line for the whole thing.
            amt = float(p.get("subtotal") or 0) or float(p.get("total") or 0)
            rows.append({"description": vendor or "Purchase", "qty": 1, "unit_price": amt,
                         "line_total": amt, "category_id": self.db.suggest_category(vendor, "")})
        self.table.setRowCount(0)
        self._load_items(rows)
        if p.get("raw_text"):
            self.receipt["raw_text"] = p["raw_text"]
        self.receipt["parse_method"] = p.get("method", "")

    def _load_items(self, items: list[dict]):
        for it in items:
            self._add_row(it)
        if not items:
            self._add_row({})

    def _add_row(self, it: dict):
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, COL_DESC, QTableWidgetItem(it.get("description", "")))
        for col, key, fmt in ((COL_QTY, "qty", "{:g}"), (COL_UNIT, "unit_price", "{:.2f}"),
                              (COL_TOTAL, "line_total", "{:.2f}")):
            v = it.get(key, 1 if key == "qty" else 0)
            cell = QTableWidgetItem(fmt.format(float(v or 0)))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, col, cell)
        cat = self._category_combo()
        cat.select_by_data(it.get("category_id", ""))
        self.table.setCellWidget(r, COL_CAT, cat)
        cls = QComboBox()
        cls.addItem("—", "")
        for c in self._classes:
            cls.addItem(c["name"], c["id"])
        cls.setCurrentIndex(max(cls.findData(it.get("class_id", "")), 0))
        self.table.setCellWidget(r, COL_CLASS, cls)
        ch = QComboBox()
        ch.addItem("Company", "")
        for p in self._people:
            ch.addItem(p["name"], f"person:{p['id']}")
        for c in self._classes:
            ch.addItem(f"Project · {c['name']}", f"class:{c['id']}")
        ch.setCurrentIndex(max(ch.findData(it.get("charged_to", "")), 0))
        self.table.setCellWidget(r, COL_CHARGED, ch)
        biz = QCheckBox()
        biz.setChecked(str(it.get("is_business", it.get("business", "1"))) not in ("0", "False"))
        biz.setToolTip("Untick for a personal item: left out if you paid, or booked as an "
                       "Owner's Draw if the company paid")
        biz.toggled.connect(self._update_check)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(biz)
        holder._chk = biz
        self.table.setCellWidget(r, COL_BIZ, holder)
        kind = QComboBox()
        for key, label in kinds_for(self.db):
            kind.addItem(label, key)
        kind.setCurrentIndex(max(kind.findData(it.get("kind", "expense")), 0))
        self.table.setCellWidget(r, COL_KIND, kind)
        self.table.blockSignals(False)
        self._update_check()

    def _remove_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows or [self.table.rowCount() - 1]:
            if r >= 0:
                self.table.removeRow(r)
        self._update_check()

    # ── live checks ──────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        r, c = item.row(), item.column()
        if c in (COL_QTY, COL_UNIT):
            qty, unit = D(self._text(r, COL_QTY) or 1), D(self._text(r, COL_UNIT))
            self.table.blockSignals(True)
            self.table.item(r, COL_TOTAL).setText(f"{cents(qty * unit):.2f}")
            self.table.blockSignals(False)
        self._update_check()

    def _text(self, r, c) -> str:
        it = self.table.item(r, c)
        return it.text().strip() if it else ""

    def _on_tax_mode(self):
        sep = self.tax_mode.currentData() == "separate"
        self.tax_cat.setVisible(sep)
        self.tip_cat.setVisible(sep)
        if sep and not self.tax_cat.current_data():
            tx = next((c for c in self._cats if c["name"] == "Taxes & Licenses"), None)
            if tx:
                self.tax_cat.select_by_data(tx["id"])

    def _update_check(self, *_):
        items = self.items()
        res = {"line_items": [{"description": i["description"], "qty": D(i["qty"]),
                               "unit_price": D(i["unit_price"]), "line_total": D(i["line_total"])}
                              for i in items],
               "subtotal": cents(self.sub_spin.value()), "tax": cents(self.tax_spin.value()),
               "tip": cents(self.tip_spin.value()), "total": cents(self.total_spin.value())}
        warns = R.check_totals(res)
        item_sum = money_sum(i["line_total"] for i in items)
        if not items:
            self.check_lbl.setText("Type the receipt's items into the table (one line is "
                                   "fine if it isn't itemised).")
            self.check_lbl.setStyleSheet(f"color:{MUTED};")
            return
        personal = money_sum(i["line_total"] for i in items if not i["business"])
        msg = f"Items: ${item_sum:,.2f}"
        if personal:
            msg += f"  ·  Personal: ${personal:,.2f}"
        if self._base_txn:
            bank = cents(abs(float(self._base_txn.get("amount") or 0)))
            booked = cents(item_sum + res["tax"] + res["tip"])
            if booked != bank:
                warns.append(f"These items total ${booked:,.2f} but the attached bank "
                             f"transaction is ${bank:,.2f} — saving would change that account's "
                             "balance.")
        if warns:
            self.check_lbl.setText("⚠ " + msg + "<br>" + "<br>".join(warns))
            self.check_lbl.setStyleSheet(f"color:{WARNING}; font-weight:600;")
        else:
            self.check_lbl.setText("✓ " + msg + " — everything adds up.")
            self.check_lbl.setStyleSheet(f"color:{SUCCESS}; font-weight:600;")

    # ── data out ─────────────────────────────────────────────────────────────

    def items(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            desc = self._text(r, COL_DESC)
            total = D(self._text(r, COL_TOTAL))
            if not desc and not total:
                continue
            holder = self.table.cellWidget(r, COL_BIZ)
            out.append({
                "description": desc, "qty": float(D(self._text(r, COL_QTY) or 1)),
                "unit_price": float(D(self._text(r, COL_UNIT))),
                "line_total": float(cents(total)),
                "category_id": self.table.cellWidget(r, COL_CAT).current_data() or "",
                "class_id": self.table.cellWidget(r, COL_CLASS).currentData() or "",
                "charged_to": self.table.cellWidget(r, COL_CHARGED).currentData() or "",
                "business": holder._chk.isChecked() if holder else True,
                "kind": self.table.cellWidget(r, COL_KIND).currentData() or "expense",
            })
        return out

    def header(self) -> dict:
        return {"id": self.receipt["id"], "vendor": self.vendor_edit.text().strip(),
                "receipt_date": self.date_edit.date().toString("yyyy-MM-dd"),
                "subtotal": self.sub_spin.value(), "tax": self.tax_spin.value(),
                "tip": self.tip_spin.value(), "total": self.total_spin.value(),
                "parse_method": self.receipt.get("parse_method", ""),
                "raw_text": self.receipt.get("raw_text", "")}

    def _persist(self, status: str = ""):
        h = self.header()
        if status:
            h["status"] = status
        self.db.save_receipt(h)
        self.db.replace_receipt_items(self.receipt["id"], self.items())

    def _save_draft(self):
        self._persist("reviewed" if self.receipt.get("status") in ("new", "", "reviewed")
                      else "")
        self.accept()

    def _suggest_all(self):
        vendor = self.vendor_edit.text().strip()
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, COL_CAT)
            if combo and not combo.current_data():
                cid = self.db.suggest_category(vendor, self._text(r, COL_DESC))
                if cid:
                    combo.select_by_data(cid)

    def _book(self):
        items = self.items()
        if not items:
            QMessageBox.warning(self, "No Items", "Add at least one line item.")
            return
        missing = [i["description"] or "(no description)" for i in items
                   if i["business"] and not i["category_id"]]
        if missing and QMessageBox.question(
                self, "Uncategorized Items",
                f"{len(missing)} business item(s) have no category and will be booked as "
                "Uncategorized. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        if self.receipt.get("status") == "booked" and QMessageBox.question(
                self, "Already Booked",
                "This receipt was already saved to the books. Replace that transaction "
                "with this version?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        base = self._base_txn
        if self.receipt.get("status") == "booked" and self.receipt.get("transaction_id"):
            base = self.db.get_transaction(self.receipt["transaction_id"])
        acct_id = (base or {}).get("account_id") or self.acct_combo.currentData()
        if not acct_id:
            QMessageBox.warning(self, "Choose an Account", "Pick the account it was paid from.")
            return
        acct = self.db.get_account(acct_id) or {}
        paid_by = self.db.owner_person_id() if (self.db.is_business() and acct.get("role") in (
            "owner_capital", "due_to_owner", "owner_paid")) else ""
        h = self.header()
        try:
            self._persist()
            self.db.book_receipt(
                self.receipt["id"], h, items, account_id=acct_id, date=h["receipt_date"],
                tax_mode=self.tax_mode.currentData(),
                tax_category_id=self.tax_cat.current_data() or "",
                tip_category_id=self.tip_cat.current_data() or "",
                paid_by=paid_by, base_txn=base)
        except ValueError as e:
            QMessageBox.warning(self, "Can't Save", str(e))
            return
        self.booked = True
        self._register_assets(items, h, acct_id)
        self.accept()

    def _register_assets(self, items, h, acct_id):
        """Offer to add each "Fixed asset" line to the asset register."""
        from utils.money import allocate
        assets = [i for i in items if i.get("kind") == "asset" and i["business"]]
        if not assets:
            return
        shares = allocate(D(h["tax"]) + D(h["tip"]), [i["line_total"] for i in items])
        rc = self.db.get_receipt(self.receipt["id"]) or {}
        from ui.dialogs.asset_dialog import AssetDialog
        for it in assets:
            share = shares[items.index(it)] if self.tax_mode.currentData() == "proportional" else 0
            dlg = AssetDialog(self.db, parent=self, preset={
                "name": it["description"], "vendor": h["vendor"],
                "purchase_date": h["receipt_date"], "in_service_date": h["receipt_date"],
                "cost": float(D(it["line_total"]) + D(share)),
                "purchase_txn_id": rc.get("transaction_id", ""),
                "receipt_id": self.receipt["id"]})
            if dlg.exec() == AssetDialog.DialogCode.Accepted:
                self.db.save_asset(dlg.get_data())

    # ── AI ───────────────────────────────────────────────────────────────────

    def _read_with_ai(self):
        from db_config import ai_settings, set_flag
        s = ai_settings()
        path = self.db.receipt_abs_path(self.receipt)
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "No File", "This receipt has no file to read.")
            return
        if not s["enabled"] or not s["api_key"]:
            QMessageBox.information(
                self, "AI Receipt Reading Is Off",
                "AI receipt reading is optional. Turn it on and paste your Anthropic API key "
                "in Settings → AI Receipt Reading.")
            return
        if not s["notice_ack"]:
            if QMessageBox.question(
                    self, "Send This Receipt to Anthropic?",
                    "AI reading uploads this receipt file to Anthropic's API so Claude can "
                    "read it. Nothing else from your books is sent.\n\nContinue? (You won't "
                    "be asked again.)",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            set_flag("ai_receipts_notice_ack", True)
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("Reading…")
        self._worker = _AIWorker(path, s["api_key"], s["model"])
        self._worker.done.connect(self._ai_done)
        self._worker.failed.connect(self._ai_failed)
        self._worker.finished.connect(self._ai_finished)
        self._worker.start()

    def _ai_done(self, res: dict):
        self._apply_parsed(res)
        self._update_check()

    def _ai_failed(self, msg: str):
        QMessageBox.warning(self, "AI Reading Failed", msg)

    def _ai_finished(self):
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("Read with AI")

    def done(self, r):
        # Closing mid-read: detach the worker and keep it alive until it ends
        # (destroying a running QThread would crash the app).
        w = self._worker
        if w is not None and w.isRunning():
            for sig in (w.done, w.failed):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            _ORPHANS.add(w)
            w.finished.connect(lambda w=w: _ORPHANS.discard(w))
        super().done(r)


_ORPHANS: set = set()
