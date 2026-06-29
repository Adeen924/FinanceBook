"""Shared reusable widgets."""
from PyQt6.QtWidgets import (QPushButton, QLabel, QFrame, QTableWidget,
                              QTableWidgetItem, QHeaderView, QHBoxLayout,
                              QWidget, QSizePolicy)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont


class NavButton(QPushButton):
    def __init__(self, text, icon="", parent=None):
        super().__init__(f"  {icon}  {text}" if icon else text, parent)
        self.setCheckable(False)
        self.setProperty("active", False)
        self.setMinimumHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, active: bool):
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class PageTitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("PageTitle")


class MutedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Muted")


class SecondaryButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Secondary")


class DangerButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Danger")


class SuccessButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Success")


class DataTable(QTableWidget):
    """Standard table with consistent styling and helpers."""
    def __init__(self, columns: list[str], parent=None):
        super().__init__(parent)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setWordWrap(False)

    def set_item(self, row: int, col: int, text: str,
                 align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                 color: str = None, bold: bool = False):
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(align)
        if color:
            item.setForeground(QColor(color))
        if bold:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        self.setItem(row, col, item)
        return item

    def money_item(self, row: int, col: int, amount: float, show_sign=False):
        from ui.styles import SUCCESS, DANGER
        if show_sign:
            text = f"+${amount:,.2f}" if amount >= 0 else f"(${abs(amount):,.2f})"
        else:
            text = f"${amount:,.2f}" if amount >= 0 else f"(${abs(amount):,.2f})"
        color = SUCCESS if amount >= 0 else DANGER
        return self.set_item(row, col, text,
                             align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             color=color, bold=True)

    def clear_rows(self):
        self.setRowCount(0)


def hbox(*widgets, spacing=8) -> QWidget:
    """Convenience: wrap widgets in an HBox container."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for item in widgets:
        if item == "stretch":
            lay.addStretch()
        elif isinstance(item, QWidget):
            lay.addWidget(item)
    return w
