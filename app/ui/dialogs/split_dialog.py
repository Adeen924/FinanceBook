"""
Split-transaction dialog.

Lets a single payment be divided across two or more lines — e.g. one Costco
charge split into "Groceries" and "Household", or a loan payment split into a
principal portion (which transfers to the loan account) and an interest portion
(a normal expense that stays in the paying account).

The lines must add up to the transaction's total before the split can be saved.

When opened for a transfer (transfer_to_id given), each line has a "→ <account>"
checkbox: checked lines MOVE to the destination account (e.g. principal paying
down a loan); unchecked lines are recorded as a spending category in the source
account (e.g. interest) and never reach the other account.

Returns the split lines via get_splits(); the caller hands them to
db.split_transaction(), which replaces the original transaction with one sibling
per line (and creates the matching transfer entry for the moved portion).
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QComboBox, QDoubleSpinBox, QLineEdit,
                             QDialogButtonBox, QScrollArea, QWidget, QFrame,
                             QCheckBox)
from PyQt6.QtCore import Qt


class SplitDialog(QDialog):
    def __init__(self, db, transaction: dict, total_amount: float = None,
                 transfer_to_id: str = "", parent=None):
        super().__init__(parent)
        self.db = db
        self.base = dict(transaction or {})
        self.setWindowTitle("Split Transaction")
        self.setMinimumWidth(620)

        self._cats = self.db.get_categories()
        self._rows: list[dict] = []   # each: {host, cat, amount, memo, transfer}

        # Transfer mode: lines can be flagged to move to this destination account.
        self._transfer_to_id = transfer_to_id or ""
        self._transfer_mode = bool(self._transfer_to_id)
        dest = self.db.get_account(self._transfer_to_id) if self._transfer_to_id else None
        self._transfer_to_name = (dest or {}).get("name", "the other account")
        src = self.db.get_account(self.base.get("account_id", ""))
        self._source_name = (src or {}).get("name", "this account")

        # Resolve the total to allocate, and any prefill lines.
        gid = self.base.get("split_group_id") or ""
        members = self.db.get_split_group(gid) if gid else []
        if members:
            # Re-editing an existing split: seed from its current lines. In
            # transfer mode amounts are stored as outflows (negative); show them
            # as positive magnitudes and remember which lines were transfers.
            raw_total = sum(float(m.get("amount") or 0) for m in members)
            self._total = round(abs(raw_total) if self._transfer_mode else raw_total, 2)
            prefill = [(m.get("category_id", ""),
                        abs(float(m.get("amount") or 0)) if self._transfer_mode
                        else float(m.get("amount") or 0),
                        m.get("memo", ""),
                        str(m.get("is_transfer", "0")) == "1") for m in members]
        else:
            base_total = float(total_amount if total_amount is not None
                               else self.base.get("amount") or 0)
            self._total = round(abs(base_total) if self._transfer_mode else base_total, 2)
            if self._transfer_mode:
                # First line = the part that moves (e.g. principal); a second line
                # starts as a stay-here expense (e.g. interest) for the user to set.
                prefill = [(self.base.get("category_id", ""), self._total, "", True),
                           ("", 0.0, "", False)]
            else:
                prefill = [(self.base.get("category_id", ""), self._total, "", False),
                           ("", 0.0, "", False)]

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

        if self._transfer_mode:
            hint = QLabel(
                f"Tick <b>“→ {self._transfer_to_name}”</b> on each part that moves to "
                f"<b>{self._transfer_to_name}</b> (e.g. loan principal). Unticked parts "
                f"are recorded as a spending category in <b>{self._source_name}</b> "
                f"(e.g. interest) and never reach {self._transfer_to_name}. "
                f"All parts must add up to the total.")
            hint.setTextFormat(Qt.TextFormat.RichText)
        else:
            hint = QLabel("Assign each part to a category. The parts must add up to the total.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # Column headers
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        cols = [("Category", 220), ("Amount", 120), ("Memo (optional)", 0)]
        if self._transfer_mode:
            cols.append((f"→ {self._transfer_to_name}", 150))
        for text, w in cols:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
            if w:
                lbl.setFixedWidth(w)
            head_row.addWidget(lbl)
        head_row.addSpacing(90)
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
        scroll.setMinimumHeight(170)
        lay.addWidget(scroll)

        # Status label and buttons are referenced by _recompute(), which runs
        # while rows are being added — so create them BEFORE the prefill rows.
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                      QDialogButtonBox.StandardButton.Cancel)
        self._btns.accepted.connect(self.accept)
        self._btns.rejected.connect(self.reject)

        for cat_id, amount, memo, transfer in prefill:
            self._add_row(cat_id, amount, memo, transfer)

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

        lay.addWidget(self._status)
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

    def _add_row(self, cat_id: str = "", amount: float = 0.0, memo: str = "",
                 transfer: bool = False):
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        cat = self._make_cat_combo(cat_id)
        cat.setFixedWidth(220)
        cat.currentIndexChanged.connect(self._recompute)
        row.addWidget(cat)

        amt = QDoubleSpinBox()
        amt.setRange(-9_999_999, 9_999_999)
        amt.setDecimals(2)
        amt.setPrefix("$")
        amt.setFixedWidth(120)
        amt.setValue(abs(float(amount)) if self._transfer_mode else float(amount))
        amt.valueChanged.connect(self._recompute)
        row.addWidget(amt)

        memo_edit = QLineEdit(memo)
        memo_edit.setPlaceholderText("e.g. interest portion")
        row.addWidget(memo_edit)

        entry = {"host": host, "cat": cat, "amount": amt,
                 "memo": memo_edit, "transfer": None}

        if self._transfer_mode:
            tr_chk = QCheckBox(f"→ {self._transfer_to_name}")
            tr_chk.setFixedWidth(150)
            tr_chk.setToolTip(
                f"Checked: this part moves to {self._transfer_to_name} "
                f"(no category needed).\nUnchecked: recorded as a spending "
                f"category in {self._source_name}.")
            tr_chk.setChecked(bool(transfer))
            tr_chk.toggled.connect(lambda on, e=entry: self._on_row_transfer_toggled(e, on))
            row.addWidget(tr_chk)
            entry["transfer"] = tr_chk
            # Transfers don't need a category — disable the combo while checked.
            cat.setEnabled(not bool(transfer))

        rest_btn = QPushButton("→ rest")
        rest_btn.setObjectName("Secondary")
        rest_btn.setFixedWidth(58)
        rest_btn.setToolTip("Put the remaining unallocated amount on this line")
        row.addWidget(rest_btn)

        rm_btn = QPushButton("✕")
        rm_btn.setObjectName("Secondary")
        rm_btn.setFixedWidth(28)
        row.addWidget(rm_btn)

        rest_btn.clicked.connect(lambda: self._assign_remaining(entry))
        rm_btn.clicked.connect(lambda: self._remove_row(entry))

        self._rows.append(entry)
        self._rows_lay.addWidget(host)
        self._recompute()

    def _on_row_transfer_toggled(self, entry, on: bool):
        # A transferred part files under "Account Transfers" automatically, so its
        # category picker is irrelevant — disable it for clarity.
        entry["cat"].setEnabled(not on)
        self._recompute()

    def _remove_row(self, entry):
        if len(self._rows) <= 2:
            return  # keep at least two lines
        self._rows.remove(entry)
        entry["host"].setParent(None)
        entry["host"].deleteLater()
        self._recompute()

    # ── allocation helpers ──────────────────────────────────────────────────────

    def _row_is_transfer(self, entry) -> bool:
        return bool(entry["transfer"]) and entry["transfer"].isChecked()

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
        # A line with an amount needs a category — UNLESS it's a transfer line,
        # which files under "Account Transfers" automatically.
        missing_cat = any(e["amount"].value() and not self._row_is_transfer(e)
                          and not e["cat"].currentData()
                          for e in self._rows)

        if balanced and not missing_cat:
            msg = f"✓ Allocated ${allocated:,.2f} — balanced."
            if self._transfer_mode:
                moved = round(sum(e["amount"].value() for e in self._rows
                                  if self._row_is_transfer(e)), 2)
                stays = round(allocated - moved, 2)
                msg += (f"  ${moved:,.2f} → {self._transfer_to_name}, "
                        f"${stays:,.2f} stays as spending in {self._source_name}.")
            self._status.setText(f"<span style='color:#15803d'>{msg}</span>")
        else:
            parts = [f"Allocated: ${allocated:,.2f}",
                     f"Remaining: ${remaining:,.2f}"]
            if missing_cat:
                parts.append("a non-transfer line with an amount has no category")
            self._status.setText(
                "<span style='color:#b45309'>" + "  ·  ".join(parts) + "</span>")
        self._status.setTextFormat(Qt.TextFormat.RichText)

        ok = self._btns.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(balanced and not missing_cat and len(self._rows) >= 2)

    # ── result ──────────────────────────────────────────────────────────────────

    def get_splits(self) -> list[dict]:
        """Lines with a non-zero amount, as dicts for db.split_transaction().

        Amounts are returned as positive magnitudes; db.split_transaction applies
        the correct sign. Each line carries a "transfer" flag (True when it should
        move to the destination account)."""
        out = []
        for e in self._rows:
            amt = round(e["amount"].value(), 2)
            if abs(amt) < 0.005:
                continue
            out.append({
                "category_id": e["cat"].currentData() or "",
                "amount":      str(amt),
                "memo":        e["memo"].text().strip(),
                "transfer":    self._row_is_transfer(e),
            })
        return out
