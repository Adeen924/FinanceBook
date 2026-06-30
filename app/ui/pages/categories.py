from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QTreeWidget, QTreeWidgetItem, QDialog, QFormLayout,
                              QLineEdit, QComboBox, QDialogButtonBox, QMessageBox,
                              QLabel)
from PyQt6.QtCore import Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QBrush
from ui.widgets import PageTitle, SecondaryButton, DangerButton, MutedLabel


# The category types a user can choose, as (display label, stored value).
# "debt_repayment" is reported in its own P&L section, excluded from Net Income.
CATEGORY_TYPES = [
    ("Expense", "expense"),
    ("Income", "income"),
    ("Debt Repayment", "debt_repayment"),
]
_TYPE_LABELS = {val: label for label, val in CATEGORY_TYPES}


def type_label(t: str) -> str:
    """Human-readable label for a stored category type value."""
    return _TYPE_LABELS.get(t or "", (t or "").replace("_", " ").title())


class CategoryDialog(QDialog):
    """Create/edit a Category (top-level) or a Sub-category (child of a Category)."""
    def __init__(self, db, parent_id="", parent=None, edit_cat=None,
                 require_parent=False, top_level=False):
        super().__init__(parent)
        self.db = db
        self.edit_cat = edit_cat
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
            for c in [c for c in db.get_categories() if not c.get("parent_id")]:
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
        cur_type = (edit_cat or {}).get("type", "expense")
        ix = self.type_combo.findData(cur_type)
        if ix >= 0:
            self.type_combo.setCurrentIndex(ix)
        form.addRow("Type", self.type_combo)

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
        cats = db.get_categories()
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


class CategoriesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._anims = []          # keep highlight animations alive while running
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(10)

        lay.addWidget(PageTitle("Categories"))

        info = MutedLabel(
            "Three tiers:  Category → Sub-category → Class.  A sub-category "
            "belongs to a category; a class belongs to a sub-category.  "
            "Double-click a row to edit it.")
        info.setWordWrap(True)
        lay.addWidget(info)

        # ── One button per tier ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_cat_btn = QPushButton("＋ Category")
        add_cat_btn.clicked.connect(self._add_category)
        add_sub_btn = SecondaryButton("＋ Sub-category")
        add_sub_btn.clicked.connect(self._add_subcategory)
        add_cls_btn = SecondaryButton("＋ Class")
        add_cls_btn.clicked.connect(self._add_class)
        del_btn = DangerButton("Delete Selected")
        del_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(add_cat_btn)
        btn_row.addWidget(add_sub_btn)
        btn_row.addWidget(add_cls_btn)
        btn_row.addStretch()
        btn_row.addWidget(del_btn)
        lay.addLayout(btn_row)

        # ── One table for the whole hierarchy ──
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Name", "Kind", "Type"])
        self._tree.setColumnWidth(0, 360)
        self._tree.setColumnWidth(1, 150)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemDoubleClicked.connect(self._edit_item)
        lay.addWidget(self._tree, 1)   # stretch factor fills the rest of the page

    def refresh(self):
        self._load()

    # ── loading ────────────────────────────────────────────────────────────────

    def _load(self):
        self._tree.clear()
        cats = self.db.get_categories()
        classes = self.db.get_classes()

        classes_by_parent = {}
        for cl in classes:
            classes_by_parent.setdefault(cl.get("parent_id", ""), []).append(cl)
        sub_ids = {s["id"] for s in cats if s.get("parent_id")}

        for cat in [c for c in cats if not c.get("parent_id")]:
            cat_item = self._make_item(cat["name"], "Category",
                                       type_label(cat.get("type", "")), "category", cat)
            for sub in [s for s in cats if s.get("parent_id") == cat["id"]]:
                sub_item = self._make_item(sub["name"], "Sub-category",
                                           type_label(sub.get("type", "")), "category", sub)
                for cl in classes_by_parent.get(sub["id"], []):
                    sub_item.addChild(self._make_item(
                        cl["name"], "Class", "—", "class", cl))
                cat_item.addChild(sub_item)
            self._tree.addTopLevelItem(cat_item)

        # Classes whose parent isn't a current sub-category (e.g. legacy data) —
        # surface them so they're never hidden and can be re-assigned or deleted.
        for cl in [c for c in classes if c.get("parent_id", "") not in sub_ids]:
            self._tree.addTopLevelItem(self._make_item(
                cl["name"], "Class (unassigned)", "—", "class", cl))

        self._tree.expandAll()

    def _make_item(self, name, kind, type_str, kind_key, data) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name, kind, type_str])
        item.setData(0, Qt.ItemDataRole.UserRole, {"kind": kind_key, "data": data})
        return item

    def _category_by_id(self, cid):
        return next((c for c in self.db.get_categories() if c["id"] == cid), None)

    # ── creating ────────────────────────────────────────────────────────────────

    def _add_category(self):
        dlg = CategoryDialog(self.db, parent=self, top_level=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = self.db.save_category(dlg.get_data())
            self._load()
            self._flash("category", saved.get("id"))

    def _add_subcategory(self):
        if not [c for c in self.db.get_categories() if not c.get("parent_id")]:
            QMessageBox.information(self, "Create a Category First",
                "A sub-category needs a parent category.\n"
                "Click “＋ Category” first.")
            return
        preset = ""
        meta = self._selected_meta()
        if meta and meta["kind"] == "category":
            d = meta["data"]
            preset = d.get("parent_id") or d["id"]
        elif meta and meta["kind"] == "class":
            sub = self._category_by_id(meta["data"].get("parent_id"))
            preset = (sub or {}).get("parent_id", "")
        dlg = CategoryDialog(self.db, parent_id=preset, parent=self, require_parent=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = self.db.save_category(dlg.get_data())
            self._load()
            self._flash("category", saved.get("id"))

    def _add_class(self):
        if not [c for c in self.db.get_categories() if c.get("parent_id")]:
            QMessageBox.information(self, "Create a Sub-category First",
                "A class needs a parent sub-category.\n"
                "Click “＋ Sub-category” first.")
            return
        preset = ""
        meta = self._selected_meta()
        if meta and meta["kind"] == "category" and meta["data"].get("parent_id"):
            preset = meta["data"]["id"]
        elif meta and meta["kind"] == "class":
            preset = meta["data"].get("parent_id", "")
        dlg = ClassDialog(self.db, parent=self, parent_subcat_id=preset)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved = self.db.save_class(dlg.get_data())
            self._load()
            self._flash("class", saved.get("id"))

    # ── editing (double-click) ───────────────────────────────────────────────────

    def _edit_item(self, item, _col):
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        if meta["kind"] == "category":
            dlg = CategoryDialog(self.db, edit_cat=meta["data"], parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.db.save_category(dlg.get_data())
                self._load()
        else:
            dlg = ClassDialog(self.db, edit_cls=meta["data"], parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.db.save_class(dlg.get_data())
                self._load()

    # ── deleting ──────────────────────────────────────────────────────────────────

    def _delete_selected(self):
        meta = self._selected_meta()
        if not meta:
            QMessageBox.information(self, "Nothing Selected",
                "Click a row in the table first, then click Delete.")
            return
        d = meta["data"]
        if meta["kind"] == "category":
            reply = QMessageBox.question(self, "Delete",
                f"Delete '{d['name']}'? Transactions assigned to it will become "
                "uncategorized.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.db.delete_category(d["id"])
                self._load()
        else:
            reply = QMessageBox.question(self, "Delete Class",
                f"Delete class '{d['name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.db.delete_class(d["id"])
                self._load()

    # ── helpers ────────────────────────────────────────────────────────────────────

    def _selected_meta(self):
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _iter_items(self):
        out = []
        def walk(it):
            out.append(it)
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        return out

    def _flash(self, kind, item_id):
        """Briefly highlight a freshly-created row, then fade it back to normal."""
        if not item_id:
            return
        target = None
        for it in self._iter_items():
            meta = it.data(0, Qt.ItemDataRole.UserRole)
            if meta and meta["kind"] == kind and meta["data"].get("id") == item_id:
                target = it
                break
        if target is None:
            return

        self._tree.scrollToItem(target)
        # Don't leave it selected — the selection highlight is what made new rows
        # hard to read. The fading highlight draws the eye instead.
        self._tree.clearSelection()
        self._tree.setCurrentItem(None)

        cols = self._tree.columnCount()
        anim = QVariantAnimation(self)
        anim.setStartValue(QColor("#fde68a"))   # warm amber
        anim.setEndValue(QColor("#ffffff"))      # fades into the table background
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
            if anim in self._anims:
                self._anims.remove(anim)

        anim.valueChanged.connect(on_val)
        anim.finished.connect(on_done)
        self._anims.append(anim)
        anim.start()
