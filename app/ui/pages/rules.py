"""Bank rules page: each rule's conditions and the account it sets."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QMessageBox, QHeaderView, QLabel)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, DataTable
from ui.styles  import SUCCESS, DANGER

ROW_H = 44

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
        hdr.addWidget(PageTitle("Rules"))
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
            "Rules set the account on transactions when you import them. A rule can "
            "match All or Any of several conditions (payee, memo, amount, date, money "
            "in / out, bank account). Higher priority rules run first — first match "
            "wins. Transactions that already have an account are never changed. "
            "Double-click a rule to edit it.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._table = DataTable(
            ["Name", "Conditions", "Account", "Priority", "Active", "Actions"])
        self._table.setColumnWidth(0, 180)
        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 200)
        self._table.setColumnWidth(3, 65)
        self._table.setColumnWidth(4, 55)
        hdr_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 170)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.cellDoubleClicked.connect(self._edit_row)
        self._rules = []
        lay.addWidget(self._table)

    def refresh(self):
        from utils.rules import describe_rule
        rules = self.db.get_rules()
        self._rules = rules
        cats  = {c["id"]: c["name"] for c in self.db.get_categories()}
        accts = {a["id"]: a["name"] for a in self.db.get_accounts(active=None)}

        self._table.clear_rows()
        self._table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            if rule.get("transfer_account_id"):
                target = "⇄ " + accts.get(rule["transfer_account_id"], "(missing account)")
            else:
                target = cats.get(rule.get("category_id", ""), "")
            active   = str(rule.get("active", "1")) == "1"
            text = describe_rule(rule, accts)

            self._table.set_item(row, 0, rule.get("name", ""), bold=True)
            item = self._table.set_item(row, 1, text)
            item.setToolTip(text)
            if target:
                self._table.set_item(row, 2, target)
            else:
                self._table.set_item(row, 2, "⚠ No account — edit to choose one",
                                     color=DANGER)
            self._table.set_item(row, 3, str(rule.get("priority", 0)),
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._table.set_item(row, 4, "Yes" if active else "No",
                                 align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                                 color=SUCCESS if active else DANGER)
            self._table.setCellWidget(row, 5, self._action_cell(rule))
            self._table.setRowHeight(row, ROW_H)

    def _edit_row(self, row: int, _col: int = 0):
        if 0 <= row < len(self._rules):
            self._edit(self._rules[row])

    def _action_cell(self, rule) -> QWidget:
        from PyQt6.QtWidgets import QHBoxLayout
        cell = QWidget()
        lay  = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)

        edit_btn = QPushButton("Edit")
        edit_btn.setObjectName("CellButton")
        edit_btn.clicked.connect(lambda _, r=rule: self._edit(r))

        del_btn = QPushButton("Delete")
        del_btn.setObjectName("CellDanger")
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
            "This will set the account on every transaction that doesn't have one "
            "yet, using your active rules (a rule that sets one of your own accounts "
            "records a transfer).\n\nTransactions that already have an account will "
            "not be changed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            count = self.db.apply_rules_to_all()
            QMessageBox.information(
                self, "Done",
                f"Applied rules to {count} transaction(s).")
