"""Auto-categorization rules page."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QMessageBox, QHeaderView, QLabel)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, DataTable

ROW_H = 44

_FIELD_LABEL = {
    "payee":  "Payee / Name",
    "memo":   "Memo",
    "amount": "Amount",
    "date":   "Date",
}
_OP_LABEL = {
    "contains":    "contains",
    "equals":      "equals",
    "starts_with": "starts with",
    "ends_with":   "ends with",
    "gt":          ">",
    "lt":          "<",
    "between":     "between",
    "day_of_month": "day of month",
}
_CELL_BTN = ("QPushButton { padding: 4px 12px; font-size: 13px; "
             "font-weight: 600; border-radius: 5px; }")


class RulesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Auto-Categorization Rules"))
        hdr.addStretch()

        apply_btn = QPushButton("Apply to All Transactions")
        apply_btn.setObjectName("Secondary")
        apply_btn.clicked.connect(self._apply_all)
        hdr.addWidget(apply_btn)

        add_btn = QPushButton("+ Add Rule")
        add_btn.clicked.connect(self._add)
        hdr.addWidget(add_btn)
        lay.addLayout(hdr)

        info = QLabel(
            "Rules auto-categorize transactions on import. "
            "Higher priority rules run first — first match wins. "
            "Already-categorized transactions are never overwritten.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._table = DataTable(
            ["Name", "Field", "Operator", "Value", "Category", "Priority", "Active", "Actions"])
        self._table.setColumnWidth(0, 180)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 150)
        self._table.setColumnWidth(4, 160)
        self._table.setColumnWidth(5, 65)
        self._table.setColumnWidth(6, 55)
        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(7, 170)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        lay.addWidget(self._table)

    def refresh(self):
        rules = self.db.get_rules()
        cats  = {c["id"]: c["name"] for c in self.db.get_categories()}

        self._table.clear_rows()
        self._table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            v  = rule.get("value", "")
            v2 = rule.get("value2", "")
            val_display = f"{v} – {v2}" if v2 else v

            cat_name = cats.get(rule.get("category_id", ""), "—")
            active   = str(rule.get("active", "1")) == "1"

            self._table.set_item(row, 0, rule.get("name", ""), bold=True)
            self._table.set_item(row, 1, _FIELD_LABEL.get(rule.get("field", ""), rule.get("field", "")))
            self._table.set_item(row, 2, _OP_LABEL.get(rule.get("operator", ""), rule.get("operator", "")))
            self._table.set_item(row, 3, val_display)
            self._table.set_item(row, 4, cat_name)
            self._table.set_item(row, 5, str(rule.get("priority", 0)),
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._table.set_item(row, 6, "Yes" if active else "No",
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                                 color="#22c55e" if active else "#ef4444")
            self._table.setCellWidget(row, 7, self._action_cell(rule))
            self._table.setRowHeight(row, ROW_H)

    def _action_cell(self, rule) -> QWidget:
        from PyQt6.QtWidgets import QHBoxLayout
        cell = QWidget()
        lay  = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)

        edit_btn = QPushButton("Edit")
        edit_btn.setStyleSheet(
            _CELL_BTN +
            "QPushButton { background: white; color: #1a1a2e; border: 1px solid #e5e7eb; }"
            "QPushButton:hover { background: #f1f5f9; }")
        edit_btn.clicked.connect(lambda _, r=rule: self._edit(r))

        del_btn = QPushButton("Delete")
        del_btn.setStyleSheet(
            _CELL_BTN +
            "QPushButton { background: #ef4444; color: white; border: none; }"
            "QPushButton:hover { background: #dc2626; }")
        del_btn.clicked.connect(lambda _, r=rule: self._delete(r))

        lay.addWidget(edit_btn)
        lay.addWidget(del_btn)
        return cell

    def _add(self):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = RuleDialog(db=self.db, parent=self)
        if dlg.exec() == RuleDialog.DialogCode.Accepted:
            self.db.save_rule(dlg.get_data())
            self.refresh()

    def _edit(self, rule):
        from ui.dialogs.rule_dialog import RuleDialog
        dlg = RuleDialog(rule=rule, db=self.db, parent=self)
        if dlg.exec() == RuleDialog.DialogCode.Accepted:
            self.db.save_rule(dlg.get_data())
            self.refresh()

    def _delete(self, rule):
        reply = QMessageBox.question(
            self, "Delete Rule",
            f"Delete rule '{rule['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_rule(rule["id"])
            self.refresh()

    def _apply_all(self):
        reply = QMessageBox.question(
            self, "Apply Rules to All Transactions",
            "This will categorize all currently-uncategorized transactions using "
            "your active rules.\n\nTransactions that already have a category will "
            "not be changed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            count = self.db.apply_rules_to_all()
            QMessageBox.information(
                self, "Done",
                f"Applied rules to {count} transaction(s).")
