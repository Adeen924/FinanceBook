from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QTreeWidget, QTreeWidgetItem, QDialog, QFormLayout,
                              QLineEdit, QComboBox, QDialogButtonBox, QMessageBox,
                              QLabel, QDoubleSpinBox, QCheckBox)
from PyQt6.QtCore import Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QBrush
from ui.widgets import PageTitle, SecondaryButton, DangerButton, MutedLabel


# The category types a user can choose, as (display label, stored value).
# "debt_repayment" is reported in its own P&L section, excluded from Net Income.
CATEGORY_TYPES = [
    ("Expense", "expense"),
    ("Income", "income"),
    ("Debt Repayment", "debt_repayment"),
    ("Asset Purchase (Balance Sheet)", "asset"),
    ("Inventory Purchase (Balance Sheet)", "inventory"),
]
_TYPE_LABELS = {val: label for label, val in CATEGORY_TYPES}


def type_label(t: str) -> str:
    """Human-readable label for a stored category type value."""
    return _TYPE_LABELS.get(t or "", (t or "").replace("_", " ").title())


class CategoryDialog(QDialog):
    """Create/edit a Category (top-level) or a Sub-category (child of a Category)."""
    def __init__(self, db, parent_id="", parent=None, edit_cat=None,
                 require_parent=False, top_level=False, category_type=""):
        super().__init__(parent)
        self.db = db
        self.edit_cat = edit_cat
        self._preset_type = category_type
        self.require_parent = require_parent
        self.parent_combo = None

        if edit_cat:
            title = "Edit Sub-category" if edit_cat.get("parent_id") else "Edit Category"
        elif require_parent:
            title = "Add Sub-category"
        else:
            title = "Add Category"
        self.setWindowTitle(title)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(edit_cat["name"] if edit_cat else "")
        self.name_edit.setPlaceholderText("e.g. HVAC" if require_parent else "e.g. Maintenance")
        form.addRow("Name *", self.name_edit)

        # A parent picker only makes sense for sub-categories, or when editing
        # (so an existing row can be re-parented). A top-level Category has none.
        if require_parent or edit_cat is not None:
            self.parent_combo = QComboBox()
            if edit_cat is not None and not require_parent:
                self.parent_combo.addItem("— Top-level category —", "")
            for c in [c for c in db.get_categories_for_book(keep_ids=[parent_id or ""])
                      if not c.get("parent_id")]:
                if not edit_cat or c["id"] != edit_cat.get("id"):
                    self.parent_combo.addItem(c["name"], c["id"])
            target = parent_id or (edit_cat or {}).get("parent_id", "")
            if target:
                for i in range(self.parent_combo.count()):
                    if self.parent_combo.itemData(i) == target:
                        self.parent_combo.setCurrentIndex(i)
                        break
            form.addRow("Parent Category" + (" *" if require_parent else ""),
                        self.parent_combo)

        self.type_combo = QComboBox()
        for label, val in CATEGORY_TYPES:
            self.type_combo.addItem(label, val)
        cur_type = (edit_cat or {}).get("type") or category_type or "expense"
        ix = self.type_combo.findData(cur_type)
        if ix >= 0:
            self.type_combo.setCurrentIndex(ix)
        form.addRow("Type", self.type_combo)

        # Business books: Schedule C line and deductible share (meals = 50%).
        self.tax_combo = None
        if db.is_business():
            from business_categories import TAX_LINES
            self.tax_combo = QComboBox()
            self.tax_combo.addItem("— Not on Schedule C —", "")
            for code, label in TAX_LINES.items():
                self.tax_combo.addItem(label, code)
            ix = self.tax_combo.findData((edit_cat or {}).get("tax_line", ""))
            self.tax_combo.setCurrentIndex(max(ix, 0))
            form.addRow("Schedule C line", self.tax_combo)
            self.pct_spin = QDoubleSpinBox()
            self.pct_spin.setRange(0, 100)
            self.pct_spin.setDecimals(0)
            self.pct_spin.setSuffix(" %")
            self.pct_spin.setValue(float((edit_cat or {}).get("deduct_pct") or 100))
            form.addRow("Deductible", self.pct_spin)
            hint = QLabel("Business meals are generally 50% deductible. "
                          "Confirm tax treatment with your accountant.")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            form.addRow("", hint)

        # A sub-category shares its parent's income/expense nature.
        if require_parent and self.parent_combo is not None:
            self.parent_combo.currentIndexChanged.connect(lambda _: self._sync_type())
            self._sync_type()
            self.type_combo.setEnabled(False)

        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _sync_type(self):
        pid = self.parent_combo.currentData()
        parent = next((c for c in self.db.get_categories() if c["id"] == pid), None)
        if parent:
            ix = self.type_combo.findData(parent.get("type", "expense"))
            if ix >= 0:
                self.type_combo.setCurrentIndex(ix)
            if getattr(self, "tax_combo", None) is not None and not self.edit_cat:
                ix = self.tax_combo.findData(parent.get("tax_line", ""))
                self.tax_combo.setCurrentIndex(max(ix, 0))
                self.pct_spin.setValue(float(parent.get("deduct_pct") or 100))

    def _ok(self):
        if not self.name_edit.text().strip():
            return
        if self.require_parent and not (self.parent_combo and self.parent_combo.currentData()):
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.edit_cat or {})
        pid = self.parent_combo.currentData() if self.parent_combo is not None else ""
        data.update({
            "name": self.name_edit.text().strip(),
            "parent_id": pid or "",
            "type": self.type_combo.currentData() or "expense",
        })
        if self.tax_combo is not None:
            data["tax_line"] = self.tax_combo.currentData() or ""
            data["deduct_pct"] = str(self.pct_spin.value())
        return data


class ClassDialog(QDialog):
    """Create/edit a Class — which belongs to a Sub-category."""
    def __init__(self, db, parent=None, edit_cls=None, parent_subcat_id=""):
        super().__init__(parent)
        self.db = db
        self.edit_cls = edit_cls
        self.setWindowTitle("Edit Class" if edit_cls else "Add Class")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit((edit_cls or {}).get("name", ""))
        self.name_edit.setPlaceholderText("e.g. Property A, Unit 12")
        form.addRow("Name *", self.name_edit)

        # Parent must be a Sub-category (a category that itself has a parent).
        self.parent_combo = QComboBox()
        cats = db.get_categories_for_book()
        cat_by_id = {c["id"]: c for c in cats}
        for sub in [c for c in cats if c.get("parent_id")]:
            top = cat_by_id.get(sub.get("parent_id"), {}).get("name", "?")
            self.parent_combo.addItem(f"{top} → {sub['name']}", sub["id"])
        target = (edit_cls or {}).get("parent_id") or parent_subcat_id
        if target:
            for i in range(self.parent_combo.count()):
                if self.parent_combo.itemData(i) == target:
                    self.parent_combo.setCurrentIndex(i)
                    break
        form.addRow("Parent Sub-category *", self.parent_combo)

        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _ok(self):
        if not self.name_edit.text().strip():
            return
        if not self.parent_combo.currentData():
            return
        self.accept()

    def get_data(self):
        data = dict(self.edit_cls or {})
        data.update({"name": self.name_edit.text().strip(),
                     "parent_id": self.parent_combo.currentData() or ""})
        return data


def flash_tree_item(owner, tree, target, anims: list):
    """Briefly highlight a freshly-created row, then fade it back to normal."""
    if target is None:
        return
    tree.scrollToItem(target)
    # Don't leave it selected — the selection highlight is what made new rows
    # hard to read. The fading highlight draws the eye instead.
    tree.clearSelection()
    tree.setCurrentItem(None)

    cols = tree.columnCount()
    anim = QVariantAnimation(owner)
    anim.setStartValue(QColor("#fde68a"))   # warm amber
    anim.setEndValue(QColor(253, 230, 138, 0))  # fades out over the glass table
    anim.setDuration(2600)

    def on_val(color):
        try:
            for c in range(cols):
                target.setBackground(c, QBrush(color))
        except RuntimeError:
            anim.stop()                       # row was removed mid-fade

    def on_done():
        try:
            for c in range(cols):
                target.setData(c, Qt.ItemDataRole.BackgroundRole, None)
        except RuntimeError:
            pass
        if anim in anims:
            anims.remove(anim)

    anim.valueChanged.connect(on_val)
    anim.finished.connect(on_done)
    anims.append(anim)
    anim.start()


class ClassesPanel(QWidget):
    """
    The "Classes" tab of the Accounts page. A class (property, project,
    campaign…) belongs to a sub-category; this lists sub-categories with their
    classes, plus any class whose sub-category is gone, so none are ever hidden.
    """
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._anims = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)

        info = MutedLabel(
            "Classes tag transactions by property, project or campaign. Each class "
            "belongs to a sub-category. Double-click a row to edit it.")
        info.setWordWrap(True)
        lay.addWidget(info)

        row = QHBoxLayout()
        self._add_btn = QPushButton("＋ Class")
        self._add_btn.clicked.connect(self._add_class)
        row.addWidget(self._add_btn)
        row.addStretch()
        del_btn = DangerButton("Delete Selected")
        del_btn.clicked.connect(self._delete_selected)
        row.addWidget(del_btn)
        lay.addLayout(row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Kind"])
        self._tree.setColumnWidth(0, 380)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemDoubleClicked.connect(self._edit_item)
        lay.addWidget(self._tree, 1)

    def refresh(self):
        self._tree.clear()
        cats = self.db.get_categories_for_book()
        by_id = {c["id"]: c for c in cats}
        classes = self.db.get_classes()
        by_parent = {}
        for cl in classes:
            by_parent.setdefault(cl.get("parent_id", ""), []).append(cl)
        for sub in [c for c in cats if c.get("parent_id")]:
            kids = by_parent.get(sub["id"], [])
            if not kids:
                continue
            top = by_id.get(sub["parent_id"], {}).get("name", "?")
            sub_item = QTreeWidgetItem([f"{top} → {sub['name']}", "Sub-category"])
            sub_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "category", "data": sub})
            for cl in kids:
                sub_item.addChild(self._class_item(cl, "Class"))
            self._tree.addTopLevelItem(sub_item)
        sub_ids = {c["id"] for c in cats if c.get("parent_id")}
        for cl in [c for c in classes if c.get("parent_id", "") not in sub_ids]:
            self._tree.addTopLevelItem(self._class_item(cl, "Class (unassigned)"))
        self._tree.expandAll()

    def _class_item(self, cl, kind):
        item = QTreeWidgetItem([cl["name"], kind])
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "class", "data": cl})
        return item

    def _selected_meta(self):
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _add_class(self):
        if not [c for c in self.db.get_categories() if c.get("parent_id")]:
            QMessageBox.information(self, "Create a Sub-category First",
                "A class needs a parent sub-category.\n"
                "Add one on the Chart of Accounts tab first (＋ Sub-account).")
            return
        preset = ""
        meta = self._selected_meta()
        if meta and meta["kind"] == "category":
            preset = meta["data"]["id"]
        elif meta and meta["kind"] == "class":
            preset = meta["data"].get("parent_id", "")
        dlg = ClassDialog(self.db, parent=self, parent_subcat_id=preset)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = self.db.save_class(dlg.get_data())
            self.refresh()
            self._flash(saved.get("id"))

    def _edit_item(self, item, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta or meta["kind"] != "class":
            return
        dlg = ClassDialog(self.db, edit_cls=meta["data"], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_class(dlg.get_data())
            self.refresh()

    def _delete_selected(self):
        meta = self._selected_meta()
        if not meta or meta["kind"] != "class":
            QMessageBox.information(self, "Nothing Selected",
                "Click a class in the list first, then click Delete.")
            return
        d = meta["data"]
        reply = QMessageBox.question(self, "Delete Class",
            f"Delete class '{d['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_class(d["id"])
            self.refresh()

    def _flash(self, class_id):
        target = None
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for it in [top] + [top.child(j) for j in range(top.childCount())]:
                meta = it.data(0, Qt.ItemDataRole.UserRole) or {}
                if meta.get("kind") == "class" and meta["data"].get("id") == class_id:
                    target = it
        flash_tree_item(self, self._tree, target, self._anims)
