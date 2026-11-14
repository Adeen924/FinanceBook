"""Add / Edit a bank rule: one or more conditions, matched All or Any, and the
account to set (an income / expense account, or a balance-sheet account, which
records matches as transfers)."""
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QSpinBox, QDialogButtonBox, QVBoxLayout, QLabel,
                              QCheckBox, QHBoxLayout, QPushButton, QWidget,
                              QMessageBox, QScrollArea, QFrame)
from PyQt6.QtCore import Qt

from ui.widgets import AccountPicker
from utils import rules as RL
from utils.chart_of_accounts import account_value, split_value

# Kept for anything that imported them from here before v1.14.
FIELDS = list(RL.FIELDS)
OPERATORS = {k: list(v) for k, v in RL.OPERATORS.items()}


class ConditionRow(QWidget):
    """One condition: [field] [operator] [value] [and value2] [✕]."""
    def __init__(self, accounts, cond=None, on_remove=None, parent=None):
        super().__init__(parent)
        self._accounts = accounts
        cond = cond or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.field_combo = QComboBox()
        for key, label in RL.FIELDS:
            self.field_combo.addItem(label, key)
        self.field_combo.setCurrentIndex(max(self.field_combo.findData(cond.get("field", "payee")), 0))
        self.field_combo.setMinimumWidth(150)
        lay.addWidget(self.field_combo)

        self.op_combo = QComboBox()
        self.op_combo.setMinimumWidth(130)
        lay.addWidget(self.op_combo)

        shown = str(cond.get("value", ""))
        if cond.get("field") == "date" and cond.get("operator") == "equals":
            iso = RL.normalize_date(shown)          # show a saved date as MM/DD/YYYY
            if iso:
                shown = f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}"
        self.value_edit = QLineEdit(shown)
        lay.addWidget(self.value_edit, 1)
        self.choice_combo = QComboBox()       # money in/out, bank account
        lay.addWidget(self.choice_combo, 1)
        self.and_lbl = QLabel("and")
        lay.addWidget(self.and_lbl)
        self.value2_edit = QLineEdit(str(cond.get("value2", "") or ""))
        self.value2_edit.setPlaceholderText("Upper value")
        self.value2_edit.setMaximumWidth(110)
        lay.addWidget(self.value2_edit)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setObjectName("Secondary")
        self.remove_btn.setFixedWidth(30)
        self.remove_btn.setToolTip("Remove this condition")
        if on_remove:
            self.remove_btn.clicked.connect(lambda: on_remove(self))
        lay.addWidget(self.remove_btn)

        self._initial = cond
        self.field_combo.currentIndexChanged.connect(self._sync)
        self.op_combo.currentIndexChanged.connect(self._sync_value2)
        self._sync()

    def field(self) -> str:
        return self.field_combo.currentData() or "payee"

    def _sync(self, *_):
        field = self.field()
        cur_op = self._initial.get("operator") if self._initial.get("field") == field else None
        self.op_combo.blockSignals(True)
        self.op_combo.clear()
        for key, label in RL.OPERATORS[field]:
            self.op_combo.addItem(label, key)
        self.op_combo.setCurrentIndex(max(self.op_combo.findData(cur_op), 0))
        self.op_combo.blockSignals(False)

        choice = field in ("direction", "account")
        self.value_edit.setVisible(not choice)
        self.choice_combo.setVisible(choice)
        if choice:
            want = self._initial.get("value") if self._initial.get("field") == field else None
            self.choice_combo.clear()
            if field == "direction":
                for key, label in RL.DIRECTIONS:
                    self.choice_combo.addItem(label, key)
            else:
                for a in self._accounts:
                    self.choice_combo.addItem(a["name"], a["id"])
            self.choice_combo.setCurrentIndex(max(self.choice_combo.findData(want), 0))
        self._sync_value2()

    def _sync_value2(self, *_):
        self.value_edit.setPlaceholderText({
            "amount": "e.g. 50.00",
            "date": ("1–31" if self.op_combo.currentData() == "day_of_month"
                     else "MM/DD/YYYY"),
        }.get(self.field(), "Text to match"))
        show = self.op_combo.currentData() == "between"
        self.and_lbl.setVisible(show)
        self.value2_edit.setVisible(show)

    def condition(self) -> dict:
        field = self.field()
        if field in ("direction", "account"):
            value = self.choice_combo.currentData() or ""
        else:
            value = self.value_edit.text().strip()
            if field == "date" and self.op_combo.currentData() == "equals":
                # Saved as YYYY-MM-DD whatever form was typed (3/15/2026 …).
                value = RL.normalize_date(value) or value
        return {"field": field, "operator": self.op_combo.currentData() or "",
                "value": value,
                "value2": self.value2_edit.text().strip()
                if self.op_combo.currentData() == "between" else ""}

    def problem(self) -> str:
        """Why this condition can't be saved, or ''."""
        return RL.condition_problem(self.condition())


class RuleDialog(QDialog):
    def __init__(self, rule: dict = None, db=None, parent=None):
        super().__init__(parent)
        self.rule = rule or {}
        self.db = db
        self.setWindowTitle("Edit Rule" if rule else "Add Rule")
        self.setMinimumWidth(640)
        self._accounts = db.get_accounts() if db else []
        self._rows: list[ConditionRow] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(self.rule.get("name", ""))
        self.name_edit.setPlaceholderText("e.g. Costco gas → Gas")
        form.addRow("Rule Name *", self.name_edit)
        lay.addLayout(form)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Match"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("All", "all")
        self.mode_combo.addItem("Any", "any")
        self.mode_combo.setCurrentIndex(max(self.mode_combo.findData(
            self.rule.get("match_mode") or "all"), 0))
        mode_row.addWidget(self.mode_combo)
        mode_row.addWidget(QLabel("of these conditions:"))
        mode_row.addStretch()
        lay.addLayout(mode_row)

        self._rows_host = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_host)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(6)
        self._rows_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._rows_host)
        scroll.setMinimumHeight(130)
        lay.addWidget(scroll)
        conds = RL.conditions_of(self.rule) if self.rule else [{"field": "payee",
                                                                "operator": "contains"}]
        for c in conds:
            self._add_row(c)

        add_btn = QPushButton("+ Add Condition")
        add_btn.setObjectName("Secondary")
        add_btn.clicked.connect(lambda: self._add_row({"field": "payee", "operator": "contains"}))
        add_row = QHBoxLayout()
        add_row.addWidget(add_btn)
        add_row.addStretch()
        lay.addLayout(add_row)

        act = QFormLayout()
        act.setSpacing(10)
        sep = QLabel("── Then (what to set on the transaction) ──────────")
        sep.setObjectName("Muted")
        act.addRow(sep)

        self.cat_combo = AccountPicker(
            self.db, keep_ids=[self.rule.get("category_id", "")],
            placeholder="Choose an account (required)")
        if self.rule.get("transfer_account_id"):
            self.cat_combo.select_by_data(account_value(self.rule["transfer_account_id"]))
        else:
            self.cat_combo.select_by_data(self.rule.get("category_id", ""))
        act.addRow("Set Account *", self.cat_combo)
        hint = QLabel("Pick one of your own accounts (bank, card, loan…) to record "
                      "matches as transfers to or from it.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        act.addRow("", hint)

        self.class_combo = QComboBox()
        self.class_combo.addItem("— No change —", "")
        for cl in (self.db.get_classes() if self.db else []):
            self.class_combo.addItem(cl["name"], cl["id"])
        self.class_combo.setCurrentIndex(max(self.class_combo.findData(
            self.rule.get("class_id", "")), 0))
        act.addRow("Set Class", self.class_combo)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100)
        self.priority_spin.setValue(int(self.rule.get("priority") or 0))
        self.priority_spin.setToolTip(
            "Higher number = checked first. First matching rule wins.")
        act.addRow("Priority  (0 = lowest)", self.priority_spin)

        self.active_check = QCheckBox("Rule is active")
        self.active_check.setChecked(str(self.rule.get("active", "1")) == "1")
        act.addRow("", self.active_check)
        lay.addLayout(act)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    # ── conditions ─────────────────────────────────────────────────────────────

    def _add_row(self, cond):
        row = ConditionRow(self._accounts, cond, on_remove=self._remove_row)
        self._rows.append(row)
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._sync_remove_buttons()
        return row

    def _remove_row(self, row):
        if len(self._rows) <= 1:
            return
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._sync_remove_buttons()

    def _sync_remove_buttons(self):
        for r in self._rows:
            r.remove_btn.setEnabled(len(self._rows) > 1)

    def conditions(self) -> list:
        return [r.condition() for r in self._rows]

    # ── accept ─────────────────────────────────────────────────────────────────

    def _accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Name Needed", "Give the rule a name.")
            self.name_edit.setFocus()
            return
        for r in self._rows:
            msg = r.problem()
            if msg:
                QMessageBox.warning(self, "Check the Conditions", msg)
                return
        kind, _target = split_value(self.cat_combo.current_data())
        if not kind:
            QMessageBox.warning(
                self, "Choose an Account",
                "A rule must set an account. Pick the income or expense account (or "
                "one of your own accounts, for a transfer) that matching "
                "transactions should go to.")
            self.cat_combo.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.rule)
        kind, target = split_value(self.cat_combo.current_data())
        conds = self.conditions()
        first = conds[0] if conds else {}
        data.update({
            "name":        self.name_edit.text().strip(),
            "conditions":  conds,
            "match_mode":  self.mode_combo.currentData() or "all",
            "field":       first.get("field", "payee"),
            "operator":    first.get("operator", "contains"),
            "value":       first.get("value", ""),
            "value2":      first.get("value2", ""),
            "category_id": target if kind == "category" else "",
            "transfer_account_id": target if kind == "account" else "",
            "class_id":    self.class_combo.currentData() or "",
            "priority":    str(self.priority_spin.value()),
            "active":      "1" if self.active_check.isChecked() else "0",
        })
        return data
