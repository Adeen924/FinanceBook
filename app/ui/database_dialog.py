"""Dialog for naming a database and choosing where to save it.

Used both for first-run setup and for creating additional databases later
from the sidebar. On first run it also offers to connect an existing database
(e.g. one another laptop keeps in OneDrive) instead of creating one.
"""
import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QFileDialog, QFrame,
                              QRadioButton, QButtonGroup, QGridLayout)


class DatabaseSetupDialog(QDialog):
    def __init__(self, parent=None, *, window_title="New Database",
                 heading="New Database", message="",
                 default_name="My Finances", default_location="",
                 accept_label="Create", show_book_type=False, show_connect=False):
        super().__init__(parent)
        self._show_book_type = show_book_type
        self._show_connect = show_connect
        self.book_type = "personal"
        # Business only: which sidebar features start on (utils.nav_config presets).
        from utils.nav_config import DEFAULT_NEW_PRESET
        self.preset = DEFAULT_NEW_PRESET
        # Set when the user connects an existing database instead of creating one.
        self.connected_path = ""
        self.setWindowTitle(window_title)
        self.setMinimumWidth(640)
        # Populated on accept; left as the defaults if the user cancels.
        self.database_name = default_name
        self.database_location = default_location
        self._build(heading, message, default_name, default_location, accept_label)

    def _build(self, heading, message, default_name, default_location, accept_label):
        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(32, 28, 32, 26)

        title_lbl = QLabel(heading)
        title_lbl.setObjectName("PageTitle")
        title_lbl.setWordWrap(True)
        lay.addWidget(title_lbl)

        if message:
            msg_lbl = QLabel(message)
            msg_lbl.setObjectName("Muted")
            msg_lbl.setWordWrap(True)
            lay.addWidget(msg_lbl)

        if self._show_connect:
            connect_hint = QLabel(
                "<b>Already use FinanceBook on another computer?</b> Connect to the "
                "database it keeps in OneDrive (or another shared folder) instead of "
                "starting a new one.")
            connect_hint.setWordWrap(True)
            connect_hint.setObjectName("InfoBox")
            lay.addWidget(connect_hint)
            connect_row = QHBoxLayout()
            self._connect_btn = QPushButton("Connect Existing Database…")
            self._connect_btn.setObjectName("ConnectButton")
            self._connect_btn.clicked.connect(self._connect_existing)
            connect_row.addWidget(self._connect_btn)
            connect_row.addStretch()
            lay.addLayout(connect_row)
            new_lbl = QLabel("Or create a new database:")
            new_lbl.setStyleSheet("font-weight:bold;")
            lay.addWidget(new_lbl)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(divider)

        name_label = QLabel("Database Name")
        name_label.setStyleSheet("font-weight:bold;")
        lay.addWidget(name_label)

        self._name_edit = QLineEdit(default_name)
        self._name_edit.setPlaceholderText("e.g. Personal Finances")
        lay.addWidget(self._name_edit)

        loc_label = QLabel("Save Location")
        loc_label.setStyleSheet("font-weight:bold;")
        lay.addWidget(loc_label)

        loc_hint = QLabel(
            "Pick any folder — a Google Drive, OneDrive, or network/server "
            "folder works well if you'd like your data backed up and synced "
            "across computers.")
        loc_hint.setObjectName("Muted")
        loc_hint.setWordWrap(True)
        lay.addWidget(loc_hint)

        loc_row = QHBoxLayout()
        self._loc_edit = QLineEdit(default_location)
        self._loc_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("Secondary")
        browse_btn.clicked.connect(self._browse)
        loc_row.addWidget(self._loc_edit)
        loc_row.addWidget(browse_btn)
        lay.addLayout(loc_row)

        if self._show_book_type:
            type_label = QLabel("What is this database for?")
            type_label.setStyleSheet("font-weight:bold;")
            lay.addWidget(type_label)
            self._personal_rb = QRadioButton(
                "Personal — household money, personal categories")
            self._business_rb = QRadioButton(
                "Business — company books: Schedule C categories, owner "
                "investment and business pages")
            self._personal_rb.setChecked(True)
            group = QButtonGroup(self)
            group.addButton(self._personal_rb)
            group.addButton(self._business_rb)
            lay.addWidget(self._personal_rb)
            lay.addWidget(self._business_rb)
            lay.addWidget(self._build_preset_box())
            self._business_rb.toggled.connect(self._on_book_type)
            self._on_book_type()
            hint = QLabel("You can switch later in Settings → Book Type.")
            hint.setObjectName("Muted")
            lay.addWidget(hint)

        lay.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("Secondary")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(accept_label)
        ok_btn.clicked.connect(self._accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

    def _build_preset_box(self) -> QFrame:
        from utils.nav_config import PRESETS
        box = QFrame()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(26, 0, 0, 0)
        bl.setSpacing(4)
        q = QLabel("How complex is your business?")
        q.setStyleSheet("font-weight:bold;")
        bl.addWidget(q)
        group = QButtonGroup(box)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        self._preset_rbs = {}
        for r, p in enumerate(PRESETS):
            rb = QRadioButton(p.label)
            # Fusion sizes radios without the QSS indicator and spacing, which then clip.
            rb.setMinimumHeight(26)
            rb.setMinimumWidth(rb.sizeHint().width() + 12)
            rb.setChecked(p.key == self.preset)
            group.addButton(rb)
            grid.addWidget(rb, r, 0)
            desc = QLabel(p.description)
            desc.setObjectName("Muted")
            grid.addWidget(desc, r, 1)
            self._preset_rbs[p.key] = rb
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        note = QLabel("Only the sidebar changes. Adjust anytime in Settings → Sidebar & Features.")
        note.setObjectName("Muted")
        bl.addWidget(note)
        self._preset_box = box
        return box

    def _on_book_type(self, *_):
        self._preset_box.setVisible(self._business_rb.isChecked())
        self.adjustSize()

    def _browse(self):
        start = self._loc_edit.text() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Choose Save Location", start)
        if path:
            self._loc_edit.setText(path)

    def _connect_existing(self):
        from ui.connect_dialog import choose_existing_database
        path = choose_existing_database(self)
        if path:
            self.connected_path = path
            self.accept()

    def _accept(self):
        name = self._name_edit.text().strip()
        location = self._loc_edit.text().strip()
        if not name or not location or not os.path.isdir(location):
            return
        self.database_name = name
        self.database_location = location
        if self._show_book_type and self._business_rb.isChecked():
            self.book_type = "business"
            self.preset = next(k for k, rb in self._preset_rbs.items() if rb.isChecked())
        self.accept()
