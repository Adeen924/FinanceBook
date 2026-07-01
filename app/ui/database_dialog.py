"""Dialog for naming a database and choosing where to save it.

Used both for first-run setup and for creating additional databases later
from the sidebar.
"""
import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QFileDialog, QFrame)


class DatabaseSetupDialog(QDialog):
    def __init__(self, parent=None, *, window_title="New Database",
                 heading="New Database", message="",
                 default_name="My Finances", default_location="",
                 accept_label="Create"):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setMinimumWidth(600)
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

    def _browse(self):
        start = self._loc_edit.text() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Choose Save Location", start)
        if path:
            self._loc_edit.setText(path)

    def _accept(self):
        name = self._name_edit.text().strip()
        location = self._loc_edit.text().strip()
        if not name or not location or not os.path.isdir(location):
            return
        self.database_name = name
        self.database_location = location
        self.accept()
