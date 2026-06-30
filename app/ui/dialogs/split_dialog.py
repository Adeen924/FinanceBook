"""
Split-transaction dialog.

Lets a single payment be divided across two or more categories — e.g. one Costco
charge split into "Groceries" and "Household". The lines must add up to the
transaction's total before the split can be saved.

Returns the split lines via get_splits(); the caller (page / edit dialog) hands
them to db.split_transaction(), which replaces the original transaction with one
sibling per line.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QDoubleSpinBox, QLineEdit,
                             QDialogButtonBox, QScrollArea, QWidget, QFrame)
from PyQt6.QtCore import Qt


class SplitDialog(QDialog):
    def __init__(self, db, transaction: dict, total_amount: float = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.base = dict(transaction or {})
        self.setWindowTitle("Split Transaction")
        self.setMinimumWidth(560)

        self._cats = self.db.get_categories()
        self._rows: list[dict] = []   # each: {widget, cat, amount, memo}

        # Resolve the total to allocate, and any prefill lines.
        gid = self.base.get("split_group_id") or ""
        members = self.db.get_split_group(gid) if gid else []
        if members:
            # Re-editing an existing split: seed from its current lines.
            self._total = round(sum(float(m.get("amount") or 0) for m in members), 2)
            prefill = [(m.get("category_id", ""), float(m.get("amount") or 0),
                        m.get("memo", "")) for m in members]
        else:
            self._total = round(
                float(total_amount if total_amount is not None
                      else self.base.get("amount") or 0), 2)
            # Start with two lines: the whole amount on the first, zero on the second.
            prefill = [(self.base.get("category_id", ""), self._total, ""),
                       ("", 0.0, "")]

        self._build(prefill)
        self._recompute()

    # ── build ──────────────────────────────────────────────────────────────────

    def _build(self, prefill):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        header = QLabel(
            f"Splitting <b>{self.base.get('payee') or self.base.get('memo') or 'transaction'}</b> "
            f"&nbsp;·&nbsp; Total to allocate: <b>${self._total:,.2f}</b>")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        lay.addWidget(header)

        hint = QLabel("Assign each part to a category. The parts must add up to the total.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # Column headers
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        for text, w in (("Category", 220), ("Amount", 130), ("Memo (optional)", 0)):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
            if w:
                lbl.setFixedWidth(w)
            head_row.addWidget(lbl)
        head_row.addSpacing(70)
        lay.addLayout(head_row)

        # Scrollable rows container
        self._rows_host = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_host)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._rows_host)
        scroll.setMinimumHeight(160)
        lay.addWidget(scroll)

        for cat_id, amount, memo in prefill:
            self._add_row(cat_id, amount, memo)

        # Add / split-evenly controls
        tools = QHBoxLayout()
        add_btn = QPushButton("+ Add Split")
        add_btn.setObjectName("Secondary")
        add_btn.clicked.connect(lambda: self._add_row())
        tools.addWidget(add_btn)

        even_btn = QPushButton("Split Evenly")
        even_btn.setObjectName("Secondary")
        even_btn.clicked.connect(self._split_evenly)
        tools.addWidget(even_btn)
        tools.addStretch()
        lay.addLayout(tools)

        # Allocation status
        self._status = QLabel("")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # OK / Cancel
        self._btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        self._btns.accepted.connect(self.accept)
        self._btns.rejected.connect(self.reject)
        lay.addWidget(self._btns)

    def _make_cat_combo(self, selected_id: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItem("— Uncategorized —", "")
        roots = [c for c in self._cats if not c.get("parent_id")]
        for cat in roots:
            combo.addItem(cat["name"], cat["id"])
            for sub in self._cats:
                if sub.get("parent_id") == cat["id"]:
                    combo.addItem(f"    ↳ {sub['name']}", sub["id"])
        for i in range(combo.count()):
            if combo.itemData(i) == selected_id:
                combo.setCurrentIndex(i)
                break
        return combo

    def _add_row(self, cat_id: str = "", amount: float = 0.0, memo: str = ""):
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        cat = self._make_cat_combo(cat_id)
        cat.setFixedWidth(220)
        row.addWidget(cat)

        amt = QDoubleSpinBox()
        amt.setRange(-9_999_999, 9_999_999)
        amt.setDecimals(2)
        amt.setPrefix("$")
        amt.setFixedWidth(130)
        amt.setValue(float(amount))
        amt.valueChanged.connect(self._recompute)
        row.addWidget(amt)

        memo_edit = QLineEdit(memo)
        memo_edit.setPlaceholderText("e.g. groceries portion")
        row.addWidget(memo_edit)

        rest_btn = QPushButton("→ rest")
        rest_btn.setObjectName("Secondary")
        rest_btn.setFixedWidth(58)
        rest_btn.setToolTip("Put the remaining unallocated amount on this line")
        row.addWidget(rest_btn)

        rm_btn = QPushButton("✕")
        rm_btn.setObjectName("Secondary")
        rm_btn.setFixedWidth(28)
        row.addWidget(rm_btn)

        entry = {"host": host, "cat": cat, "amount": amt, "memo": memo_edit}
        rest_btn.clicked.connect(lambda: self._assign_remaining(entry))
        rm_btn.clicked.connect(lambda: self._remove_row(entry))

        self._rows.append(entry)
        self._rows_lay.addWidget(host)
        self._recompute()

    def _remove_row(self, entry):
        if len(self._rows) <= 2:
            return  # keep at least two lines
        self._rows.remove(entry)
        entry["host"].setParent(None)
        entry["host"].deleteLater()
        self._recompute()

    # ── allocation helpers ──────────────────────────────────────────────────────

    def _allocated(self) -> float:
        return round(sum(e["amount"].value() for e in self._rows), 2)

    def _remaining(self) -> float:
        return round(self._total - self._allocated(), 2)

    def _assign_remaining(self, entry):
        entry["amount"].setValue(round(entry["amount"].value() + self._remaining(), 2))

    def _split_evenly(self):
        n = len(self._rows)
        if n == 0:
            return
        share = round(self._total / n, 2)
        for e in self._rows[:-1]:
            e["amount"].setValue(share)
        # Last line absorbs the rounding remainder so the total stays exact.
        self._rows[-1]["amount"].setValue(round(self._total - share * (n - 1), 2))

    def _recompute(self):
        remaining = self._remaining()
        allocated = self._allocated()
        balanced = abs(remaining) < 0.005
        missing_cat = any(e["amount"].value() and not e["cat"].currentData()
                          for e in self._rows)

        if balanced and not missing_cat:
            self._status.setText(
                f"<span style='color:#15803d'>✓ Allocated ${allocated:,.2f} "
                f"— balanced.</span>")
        else:
            parts = [f"Allocated: ${allocated:,.2f}",
                     f"Remaining: ${remaining:,.2f}"]
            if missing_cat:
                parts.append("a line with an amount has no category")
            self._status.setText(
                "<span style='color:#b45309'>" + "  ·  ".join(parts) + "</span>")
        self._status.setTextFormat(Qt.TextFormat.RichText)

        ok = self._btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(balanced and not missing_cat and len(self._rows) >= 2)

    # ── result ──────────────────────────────────────────────────────────────────

    def get_splits(self) -> list[dict]:
        """Lines with a non-zero amount, as dicts for db.split_transaction()."""
        out = []
        for e in self._rows:
            amt = round(e["amount"].value(), 2)
            if abs(amt) < 0.005:
                continue
            out.append({
                "category_id": e["cat"].currentData() or "",
                "amount":      str(amt),
                "memo":        e["memo"].text().strip(),
            })
        return out
