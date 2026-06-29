"""Add / Edit auto-categorization rule."""
from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                              QSpinBox, QDialogButtonBox, QVBoxLayout, QLabel,
                              QCheckBox)
from PyQt6.QtCore import Qt


FIELDS = [
    ("payee",  "Payee / Name"),
    ("memo",   "Memo"),
    ("amount", "Amount ($)"),
    ("date",   "Date"),
]

OPERATORS = {
    "payee":  [("contains",    "contains"),
               ("equals",      "equals (exact)"),
               ("starts_with", "starts with"),
               ("ends_with",   "ends with")],
    "memo":   [("contains",    "contains"),
               ("equals",      "equals (exact)"),
               ("starts_with", "starts with"),
               ("ends_with",   "ends with")],
    "amount": [("equals",  "= equals"),
               ("gt",      "> greater than"),
               ("lt",      "< less than"),
               ("between", "between two values")],
    "date":   [("equals",       "= specific date  (YYYY-MM-DD)"),
               ("day_of_month", "day of month  (1 – 31)")],
}


class RuleDialog(QDialog):
    def __init__(self, rule: dict = None, db=None, parent=None):
        super().__init__(parent)
        self.rule = rule or {}
        self.db   = db
        self.setWindowTitle("Edit Rule" if rule else "Add Rule")
        self.setMinimumWidth(480)
        self._build()
        self._update_operators()

    def _build(self):
        lay  = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        # Name
        self.name_edit = QLineEdit(self.rule.get("name", ""))
        self.name_edit.setPlaceholderText("e.g. Costco Gas → Gas")
        form.addRow("Rule Name *", self.name_edit)

        # Field
        self.field_combo = QComboBox()
        for key, label in FIELDS:
            self.field_combo.addItem(label, key)
        cur_field = self.rule.get("field", "payee")
        self.field_combo.setCurrentIndex(
            next((i for i, (k, _) in enumerate(FIELDS) if k == cur_field), 0))
        self.field_combo.currentIndexChanged.connect(self._update_operators)
        form.addRow("Match Field", self.field_combo)

        # Operator (populated dynamically)
        self.op_combo = QComboBox()
        self.op_combo.currentIndexChanged.connect(self._toggle_value2)
        form.addRow("Operator", self.op_combo)

        # Value
        self.value_edit = QLineEdit(self.rule.get("value", ""))
        self.value_edit.setPlaceholderText("Value to match against")
        form.addRow("Value *", self.value_edit)

        # Value2 (shown only for 'between')
        self._v2_row_label = QLabel("Upper Value")
        self.value2_edit   = QLineEdit(self.rule.get("value2", ""))
        self.value2_edit.setPlaceholderText("Upper bound")
        form.addRow(self._v2_row_label, self.value2_edit)

        # Separator
        sep = QLabel("── Action (what to set on the transaction) ──────────")
        sep.setObjectName("Muted")
        form.addRow(sep)

        # Category
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("— No change —", "")
        cats = self.db.get_categories() if self.db else []
        for c in cats:
            prefix = "    " if c.get("parent_id") else ""
            self.cat_combo.addItem(prefix + c["name"], c["id"])
        cur_cat = self.rule.get("category_id", "")
        self.cat_combo.setCurrentIndex(
            next((i for i in range(self.cat_combo.count())
                  if self.cat_combo.itemData(i) == cur_cat), 0))
        form.addRow("Set Category", self.cat_combo)

        # Class
        self.class_combo = QComboBox()
        self.class_combo.addItem("— No change —", "")
        classes = self.db.get_classes() if self.db else []
        for cl in classes:
            self.class_combo.addItem(cl["name"], cl["id"])
        cur_class = self.rule.get("class_id", "")
        self.class_combo.setCurrentIndex(
            next((i for i in range(self.class_combo.count())
                  if self.class_combo.itemData(i) == cur_class), 0))
        form.addRow("Set Class", self.class_combo)

        # Priority
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100)
        self.priority_spin.setValue(int(self.rule.get("priority") or 0))
        self.priority_spin.setToolTip(
            "Higher number = checked first. First matching rule wins.")
        form.addRow("Priority  (0 = lowest)", self.priority_spin)

        # Active
        self.active_check = QCheckBox("Rule is active")
        self.active_check.setChecked(str(self.rule.get("active", "1")) == "1")
        form.addRow("", self.active_check)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    # ── slots ──────────────────────────────────────────────────────────────────

    def _update_operators(self):
        field = self.field_combo.currentData() or "payee"
        ops   = OPERATORS.get(field, OPERATORS["payee"])
        cur   = self.rule.get("operator", ops[0][0])

        self.op_combo.blockSignals(True)
        self.op_combo.clear()
        for key, label in ops:
            self.op_combo.addItem(label, key)
        self.op_combo.setCurrentIndex(
            next((i for i, (k, _) in enumerate(ops) if k == cur), 0))
        self.op_combo.blockSignals(False)
        self._toggle_value2()

    def _toggle_value2(self):
        show = (self.op_combo.currentData() == "between")
        self._v2_row_label.setVisible(show)
        self.value2_edit.setVisible(show)

    def _accept(self):
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        if not self.value_edit.text().strip():
            self.value_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.rule)
        data.update({
            "name":        self.name_edit.text().strip(),
            "field":       self.field_combo.currentData(),
            "operator":    self.op_combo.currentData(),
            "value":       self.value_edit.text().strip(),
            "value2":      self.value2_edit.text().strip(),
            "category_id": self.cat_combo.currentData() or "",
            "class_id":    self.class_combo.currentData() or "",
            "priority":    str(self.priority_spin.value()),
            "active":      "1" if self.active_check.isChecked() else "0",
        })
        return data
