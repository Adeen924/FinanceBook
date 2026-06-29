"""First-run Google Sheets setup dialog."""
import json
import os
import shutil
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QLineEdit, QFileDialog,
                              QTextBrowser, QMessageBox, QFrame)
from PyQt6.QtCore import Qt


CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
CREDS_PATH  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "credentials.json")


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FinanceBook — First-Time Setup")
        self.setMinimumWidth(620)
        self.setMinimumHeight(520)
        self._creds_file = ""
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(28, 24, 28, 24)

        title = QLabel("Connect to Google Sheets")
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        sub = QLabel("FinanceBook stores all data in a Google Sheet you own.\nThis setup takes about 5 minutes and only needs to be done once.")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # Instructions box
        instructions = QTextBrowser()
        instructions.setMaximumHeight(200)
        instructions.setOpenExternalLinks(True)
        instructions.setHtml("""
        <ol style="margin:8px; line-height:1.8;">
          <li>Go to <a href="https://console.cloud.google.com">console.cloud.google.com</a> and create a project.</li>
          <li>Enable the <b>Google Sheets API</b> for that project.</li>
          <li>Go to <b>APIs &amp; Services → Credentials → Create Credentials → Service Account</b>.</li>
          <li>Once created, click the service account → <b>Keys → Add Key → JSON</b>. Download that file.</li>
          <li>Create a new <a href="https://sheets.google.com">Google Sheet</a> and copy its ID from the URL:
              <code>docs.google.com/spreadsheets/d/<b>SHEET_ID</b>/edit</code></li>
          <li>Share the Sheet with the <b>client_email</b> address inside the JSON file — give it <b>Editor</b> access.</li>
        </ol>
        """)
        lay.addWidget(instructions)

        # Credentials file picker
        creds_label = QLabel("Service Account JSON File")
        creds_label.setStyleSheet("font-weight:bold;")
        lay.addWidget(creds_label)

        creds_row = QHBoxLayout()
        self._creds_edit = QLineEdit()
        self._creds_edit.setPlaceholderText("Select the downloaded credentials JSON file…")
        self._creds_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("Secondary")
        browse_btn.clicked.connect(self._browse_creds)
        creds_row.addWidget(self._creds_edit)
        creds_row.addWidget(browse_btn)
        lay.addLayout(creds_row)

        # Spreadsheet ID
        sheet_label = QLabel("Google Spreadsheet ID")
        sheet_label.setStyleSheet("font-weight:bold;")
        lay.addWidget(sheet_label)

        self._sheet_edit = QLineEdit()
        self._sheet_edit.setPlaceholderText("e.g. 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms")
        lay.addWidget(self._sheet_edit)

        hint = QLabel("Copy from the spreadsheet URL between /d/ and /edit")
        hint.setObjectName("Muted")
        lay.addWidget(hint)

        lay.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("Secondary")
        cancel_btn.clicked.connect(self.reject)
        connect_btn = QPushButton("Connect & Save")
        connect_btn.clicked.connect(self._connect)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(connect_btn)
        lay.addLayout(btn_row)

    def _browse_creds(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select credentials JSON",
                                               "", "JSON files (*.json)")
        if path:
            self._creds_file = path
            self._creds_edit.setText(path)

    def _connect(self):
        if not self._creds_file:
            QMessageBox.warning(self, "Missing File", "Please select your credentials JSON file.")
            return
        sheet_id = self._sheet_edit.text().strip()
        if not sheet_id:
            QMessageBox.warning(self, "Missing ID", "Please enter your Google Spreadsheet ID.")
            return

        # Test the connection
        try:
            from sheets.client import SheetsClient
            shutil.copy(self._creds_file, CREDS_PATH)
            client = SheetsClient(CREDS_PATH, sheet_id)
            cfg = {"credentials_path": CREDS_PATH, "spreadsheet_id": sheet_id}
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
            QMessageBox.information(self, "Success",
                "Connected successfully! FinanceBook will now open.")
            self.accept()
        except Exception as e:
            if os.path.exists(CREDS_PATH):
                os.remove(CREDS_PATH)
            QMessageBox.critical(self, "Connection Failed",
                f"Could not connect to Google Sheets:\n\n{e}\n\n"
                "Check that the Sheet is shared with the service account email.")
