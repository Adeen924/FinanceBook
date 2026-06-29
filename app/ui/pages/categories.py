from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QTreeWidget, QTreeWidgetItem, QDialog, QFormLayout,
                              QLineEdit, QComboBox, QDialogButtonBox, QMessageBox,
                              QSplitter, QLabel, QListWidget, QListWidgetItem,
                              QGroupBox)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, SecondaryButton, DangerButton


class CategoryDialog(QDialog):
    def __init__(self, db, parent_id="", cat_type="expense", parent=None, edit_cat=None):
        super().__init__(parent)
        self.db = db
        self.edit_cat = edit_cat
        self.setWindowTitle("Edit Category" if edit_cat else "Add Category")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(edit_cat["name"] if edit_cat else "")
        self.name_edit.setPlaceholderText("e.g. Maintenance")
        form.addRow("Name *", self.name_edit)

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("— Top-level category —", "")
        cats = [c for c in db.get_categories() if not c.get("parent_id")]
        for c in cats:
            if not edit_cat or c["id"] != edit_cat.get("id"):
                self.parent_combo.addItem(c["name"], c["id"])
        if parent_id:
            for i in range(self.parent_combo.count()):
                if self.parent_combo.itemData(i) == parent_id:
                    self.parent_combo.setCurrentIndex(i)
                    break
        form.addRow("Parent Category", self.parent_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["expense", "income"])
        if (edit_cat or {}).get("type") == "income":
            self.type_combo.setCurrentIndex(1)
        form.addRow("Type", self.type_combo)

        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _ok(self):
        if not self.name_edit.text().strip():
            return
        self.accept()

    def get_data(self) -> dict:
        data = dict(self.edit_cat or {})
        data.update({
            "name": self.name_edit.text().strip(),
            "parent_id": self.parent_combo.currentData() or "",
            "type": self.type_combo.currentText(),
        })
        return data


class ClassDialog(QDialog):
    def __init__(self, db, parent=None, edit_cls=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Class" if edit_cls else "Add Class")
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit((edit_cls or {}).get("name",""))
        self.name_edit.setPlaceholderText("e.g. Property A, Marketing")
        form.addRow("Name *", self.name_edit)
        self.parent_combo = QComboBox()
        self.parent_combo.addItem("— Top-level —", "")
        for c in db.get_classes():
            if not edit_cls or c["id"] != (edit_cls or {}).get("id"):
                self.parent_combo.addItem(c["name"], c["id"])
        form.addRow("Parent Class", self.parent_combo)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(lambda: self.accept() if self.name_edit.text().strip() else None)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.edit_cls = edit_cls

    def get_data(self):
        data = dict(self.edit_cls or {})
        data.update({"name": self.name_edit.text().strip(),
                     "parent_id": self.parent_combo.currentData() or ""})
        return data


class CategoriesPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)
        lay.addWidget(PageTitle("Categories & Classes"))

        info = QLabel(
            "Categories organize what money is for (e.g. Maintenance → HVAC).  "
            "Classes are a separate dimension for departments, properties, or projects.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Categories panel ──
        cat_panel = QWidget()
        cat_lay = QVBoxLayout(cat_panel)
        cat_lay.setContentsMargins(0,0,0,0)
        cat_lay.setSpacing(8)

        cat_hdr = QHBoxLayout()
        cat_hdr.addWidget(QLabel("Categories"))
        cat_hdr.addStretch()
        add_cat_btn = QPushButton("+ Add Category")
        add_cat_btn.clicked.connect(lambda: self._add_cat())
        cat_hdr.addWidget(add_cat_btn)
        cat_lay.addLayout(cat_hdr)

        self._cat_tree = QTreeWidget()
        self._cat_tree.setHeaderLabels(["Name", "Type"])
        self._cat_tree.setColumnWidth(0, 240)
        self._cat_tree.setAlternatingRowColors(True)
        cat_lay.addWidget(self._cat_tree)

        cat_actions = QHBoxLayout()
        add_sub_btn = SecondaryButton("+ Sub-category")
        add_sub_btn.clicked.connect(self._add_sub)
        del_cat_btn = DangerButton("Delete Selected")
        del_cat_btn.clicked.connect(self._delete_cat)
        cat_actions.addWidget(add_sub_btn)
        cat_actions.addStretch()
        cat_actions.addWidget(del_cat_btn)
        cat_lay.addLayout(cat_actions)

        splitter.addWidget(cat_panel)

        # ── Classes panel ──
        cls_panel = QWidget()
        cls_lay = QVBoxLayout(cls_panel)
        cls_lay.setContentsMargins(0,0,0,0)
        cls_lay.setSpacing(8)

        cls_hdr = QHBoxLayout()
        cls_hdr.addWidget(QLabel("Classes"))
        cls_hdr.addStretch()
        add_cls_btn = QPushButton("+ Add Class")
        add_cls_btn.clicked.connect(self._add_cls)
        cls_hdr.addWidget(add_cls_btn)
        cls_lay.addLayout(cls_hdr)

        self._cls_list = QListWidget()
        self._cls_list.setAlternatingRowColors(True)
        cls_lay.addWidget(self._cls_list)

        del_cls_btn = DangerButton("Delete Selected")
        del_cls_btn.clicked.connect(self._delete_cls)
        cls_lay.addWidget(del_cls_btn)

        splitter.addWidget(cls_panel)
        splitter.setSizes([500, 300])
        lay.addWidget(splitter)

    def refresh(self):
        self._load_cats()
        self._load_classes()

    def _load_cats(self):
        self._cat_tree.clear()
        cats = self.db.get_categories()
        cat_by_id = {c["id"]: c for c in cats}
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            item = QTreeWidgetItem([cat["name"], cat.get("type","").title()])
            item.setData(0, Qt.ItemDataRole.UserRole, cat)
            for sub in cats:
                if sub.get("parent_id") == cat["id"]:
                    sub_item = QTreeWidgetItem([sub["name"], sub.get("type","").title()])
                    sub_item.setData(0, Qt.ItemDataRole.UserRole, sub)
                    item.addChild(sub_item)
            self._cat_tree.addTopLevelItem(item)
            item.setExpanded(True)

    def _load_classes(self):
        self._cls_list.clear()
        for cls in self.db.get_classes():
            item = QListWidgetItem(cls["name"])
            item.setData(Qt.ItemDataRole.UserRole, cls)
            self._cls_list.addItem(item)

    def _add_cat(self, parent_id=""):
        dlg = CategoryDialog(self.db, parent_id=parent_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_category(dlg.get_data())
            self._load_cats()

    def _add_sub(self):
        item = self._cat_tree.currentItem()
        if not item:
            QMessageBox.information(self, "Select Parent",
                "Click a top-level category first, then click '+ Sub-category'.")
            return
        cat = item.data(0, Qt.ItemDataRole.UserRole)
        # Walk up to top-level
        while cat.get("parent_id"):
            cat = next((c for c in self.db.get_categories()
                        if c["id"] == cat.get("parent_id")), cat)
            break
        dlg = CategoryDialog(self.db, parent_id=cat["id"], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_category(dlg.get_data())
            self._load_cats()

    def _delete_cat(self):
        item = self._cat_tree.currentItem()
        if not item:
            return
        cat = item.data(0, Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(self, "Delete Category",
            f"Delete '{cat['name']}'? Transactions assigned to it will become uncategorized.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_category(cat["id"])
            self._load_cats()

    def _add_cls(self):
        dlg = ClassDialog(self.db, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.save_class(dlg.get_data())
            self._load_classes()

    def _delete_cls(self):
        item = self._cls_list.currentItem()
        if not item:
            return
        cls = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(self, "Delete Class",
            f"Delete class '{cls['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_class(cls["id"])
            self._load_classes()
