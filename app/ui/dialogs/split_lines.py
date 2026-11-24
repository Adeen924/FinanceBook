"""
The Transaction window's split grid (v1.19).

A split is ONE transaction (one bank / card entry, one payee, one total) filed
under several accounts. SplitLinesEditor is the lines grid shown under the
transaction's shared fields when "Split" is on: Account · Memo · Class ·
Amount per line, a "Remaining: $x" line and "+ Add line".

Amounts are entered in the transaction's own direction: with Money out picked,
a line of $30 is $30 of the purchase; a negative line goes the other way (a
coupon or a returned item on the same receipt). The lines must add up to the
transaction's amount before it can be saved.

Remaining amount (T2): a new line starts with whatever is still unallocated,
and while the user edits the other lines the LAST line keeps absorbing the
remainder — until the user types in that last line themself. "→ rest" on any
line puts the remainder there.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QComboBox, QDoubleSpinBox, QLineEdit, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, pyqtSignal
from ui.widgets import AccountPicker


class SplitLinesEditor(QWidget):
    changed = pyqtSignal()

    COL_ACCOUNT, COL_MEMO, COL_CLASS, COL_AMOUNT = 230, 0, 150, 120

    def __init__(self, db, account_id: str = "", parent=None, money_direction=None):
        super().__init__(parent)
        self.db = db
        self._account_id = account_id or ""
        self._total = 0.0
        self._auto = False          # True while amounts are set by code
        self.money_direction = money_direction
        self._classes = db.get_classes()
        self._rows: list[dict] = []
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        for text, w in (("Account", self.COL_ACCOUNT), ("Memo", self.COL_MEMO),
                        ("Class", self.COL_CLASS), ("Amount", self.COL_AMOUNT)):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
            if w:
                lbl.setFixedWidth(w)
            head.addWidget(lbl, 0 if w else 1)
        head.addSpacing(64 + 28 + 12)
        lay.addLayout(head)

        self._rows_host = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_host)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(6)
        self._rows_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._rows_host)
        scroll.setMinimumHeight(120)
        lay.addWidget(scroll, 1)

        tools = QHBoxLayout()
        self._add_btn = QPushButton("+ Add line")
        self._add_btn.setObjectName("Secondary")
        self._add_btn.clicked.connect(lambda: self.add_line())
        tools.addWidget(self._add_btn)
        self._fill_btn = QPushButton("Fill remaining")
        self._fill_btn.setObjectName("Secondary")
        self._fill_btn.setToolTip("Put the unallocated amount on the last line")
        self._fill_btn.clicked.connect(self.fill_remaining)
        tools.addWidget(self._fill_btn)
        tools.addStretch()
        self._remaining = QLabel("")
        self._remaining.setTextFormat(Qt.TextFormat.RichText)
        tools.addWidget(self._remaining)
        lay.addLayout(tools)

    def _make_class_combo(self, class_id: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("—", "")
        for c in self._classes:
            combo.addItem(c.get("name", ""), c["id"])
        ix = combo.findData(class_id or "")
        combo.setCurrentIndex(max(ix, 0))
        return combo

    # ── rows ──────────────────────────────────────────────────────────────────

    def add_line(self, value: str = "", memo: str = "", class_id: str = "",
                 amount: float | None = None, typed: bool = False) -> dict:
        """Add a line; amount None = the remaining amount (T2)."""
        if amount is None:
            amount = max(self.remaining(), 0.0)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        acct = AccountPicker(self.db, exclude_account_id=self._account_id,
                             keep_ids=[v for v in (value,) if v and not v.startswith("acct:")],
                             placeholder="Account")
        acct.money_direction = self._direction
        acct.select_by_data(value)
        acct._last_value = acct.current_data()
        acct.setFixedWidth(self.COL_ACCOUNT)
        row.addWidget(acct)

        memo_edit = QLineEdit(memo or "")
        memo_edit.setPlaceholderText("Memo (optional)")
        row.addWidget(memo_edit, 1)

        cls = self._make_class_combo(class_id)
        cls.setFixedWidth(self.COL_CLASS)
        row.addWidget(cls)

        amt = QDoubleSpinBox()
        amt.setRange(-9_999_999, 9_999_999)
        amt.setDecimals(2)
        amt.setPrefix("$")
        amt.setFixedWidth(self.COL_AMOUNT)
        self._auto = True
        amt.setValue(float(amount or 0))
        self._auto = False
        row.addWidget(amt)

        rest_btn = QPushButton("→ rest")
        rest_btn.setObjectName("Secondary")
        rest_btn.setFixedWidth(64)
        rest_btn.setStyleSheet("padding: 6px 4px;")
        rest_btn.setToolTip("Put the remaining unallocated amount on this line")
        row.addWidget(rest_btn)
        rm_btn = QPushButton("✕")
        rm_btn.setObjectName("Secondary")
        rm_btn.setFixedWidth(28)
        rm_btn.setToolTip("Remove this line")
        row.addWidget(rm_btn)

        entry = {"host": host, "acct": acct, "memo": memo_edit, "cls": cls,
                 "amount": amt, "typed": typed}
        amt.valueChanged.connect(lambda _v, e=entry: self._on_amount(e))
        acct.currentIndexChanged.connect(lambda _i: self.changed.emit())
        acct.lineEdit().textChanged.connect(lambda _t: self.changed.emit())
        rest_btn.clicked.connect(lambda: self.assign_remaining(entry))
        rm_btn.clicked.connect(lambda: self.remove_line(entry))

        self._rows.append(entry)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, host)
        self._refresh()
        return entry

    def remove_line(self, entry):
        if entry not in self._rows:
            return
        self._rows.remove(entry)
        entry["host"].setParent(None)
        entry["host"].deleteLater()
        self._rebalance_last()
        self._refresh()

    def clear(self):
        for e in list(self._rows):
            e["host"].setParent(None)
            e["host"].deleteLater()
        self._rows = []
        self._refresh()

    def set_lines(self, lines):
        """lines: [(value, memo, class_id, amount)] — loaded lines count as
        not typed, so the last one follows the remainder until edited."""
        self.clear()
        for value, memo, class_id, amount in lines:
            self.add_line(value, memo, class_id, amount, typed=False)

    def rows(self) -> list[dict]:
        return list(self._rows)

    # ── amounts ───────────────────────────────────────────────────────────────

    def _direction(self) -> str:
        return self.money_direction() if callable(self.money_direction) else "out"

    def set_total(self, total: float):
        self._total = round(float(total or 0), 2)
        self._rebalance_last()
        self._refresh()

    def total(self) -> float:
        return self._total

    def set_account(self, account_id: str):
        self._account_id = account_id or ""
        for e in self._rows:
            e["acct"].set_exclude_account(self._account_id)

    def allocated(self) -> float:
        return round(sum(e["amount"].value() for e in self._rows), 2)

    def remaining(self) -> float:
        return round(self._total - self.allocated(), 2)

    def _set_amount(self, entry, value: float):
        self._auto = True
        try:
            entry["amount"].setValue(round(value, 2))
        finally:
            self._auto = False

    def _on_amount(self, entry):
        if not self._auto:
            entry["typed"] = True
            if entry is not (self._rows[-1] if self._rows else None):
                self._rebalance_last()
        self._refresh()

    def _rebalance_last(self):
        """Keep the last line equal to what the others leave, unless the user
        typed in it (or it is the only line)."""
        if len(self._rows) < 2:
            return
        last = self._rows[-1]
        if last["typed"]:
            return
        others = round(sum(e["amount"].value() for e in self._rows[:-1]), 2)
        self._set_amount(last, max(round(self._total - others, 2), 0.0))

    def assign_remaining(self, entry):
        self._set_amount(entry, entry["amount"].value() + self.remaining())
        entry["typed"] = True
        self._refresh()

    def fill_remaining(self):
        if self._rows:
            self.assign_remaining(self._rows[-1])

    def is_balanced(self) -> bool:
        return abs(self.remaining()) < 0.005

    def line_count(self) -> int:
        return len([e for e in self._rows if abs(e["amount"].value()) >= 0.005])

    def missing_account(self) -> bool:
        return any(abs(e["amount"].value()) >= 0.005 and not e["acct"].current_data()
                   for e in self._rows)

    def unknown_pickers(self) -> list:
        return [e["acct"] for e in self._rows if e["acct"].unknown_text()]

    def lines(self) -> list[dict]:
        """[{value, memo, class_id, amount}] for lines with an amount; amount
        in the transaction's direction (positive = same way as the total)."""
        out = []
        for e in self._rows:
            amt = round(e["amount"].value(), 2)
            if abs(amt) < 0.005:
                continue
            out.append({"value": e["acct"].current_data() or "",
                        "memo": e["memo"].text().strip(),
                        "class_id": e["cls"].currentData() or "",
                        "amount": amt})
        return out

    def _refresh(self):
        rem = self.remaining()
        if abs(rem) < 0.005:
            text = (f"<span style='color:#15803d'>✓ Lines add up to "
                    f"${self._total:,.2f}</span>")
        else:
            text = (f"<span style='color:#b45309'><b>Remaining: ${rem:,.2f}</b> "
                    f"(of ${self._total:,.2f})</span>")
        self._remaining.setText(text)
        self.changed.emit()

    def remaining_text(self) -> str:
        return self._remaining.text()
