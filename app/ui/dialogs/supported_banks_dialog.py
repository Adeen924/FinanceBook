"""Import page → "Supported banks…" (v1.16): the bank CSV exports FinanceBook
recognises, from parsers/bank_formats.REGISTRY."""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView

from ui.widgets import DataTable, SecondaryButton
from ui.styles import SUCCESS, MUTED

COLUMNS = ["Bank", "Account kind", "How the sign is read", "Verified"]


class SupportedBanksDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from parsers import bank_formats
        self.setWindowTitle("Supported Banks")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "FinanceBook recognises these bank CSV exports from their header row and "
            "reads money in / money out the way each bank writes it. <b>Verified</b> "
            "formats were checked against a real export; the others follow the bank's "
            "documented layout. Any other CSV or Excel file still imports with automatic "
            "column detection — check the preview, and use “Flip all signs” if money "
            "in and out are the wrong way round.")
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        lay.addWidget(intro)

        self.table = DataTable(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 120)
        formats = bank_formats.REGISTRY
        self.table.setRowCount(len(formats))
        for r, f in enumerate(formats):
            self.table.set_item(r, 0, f.display_bank, bold=True)
            self.table.set_item(r, 1, bank_formats.KIND_TEXT[f.kind])
            item = self.table.set_item(r, 2, bank_formats.SIGN_STYLE_TEXT[f.sign])
            item.setToolTip("\n".join(f.quirks))
            self.table.set_item(r, 3, "Yes" if f.verified else "Not yet",
                                color=SUCCESS if f.verified else MUTED)
        lay.addWidget(self.table)

        btns = QHBoxLayout()
        btns.addStretch()
        close = SecondaryButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)
