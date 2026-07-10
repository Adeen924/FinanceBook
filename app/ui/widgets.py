"""Shared reusable widgets."""
from PyQt6.QtWidgets import (QPushButton, QLabel, QFrame, QTableWidget,
                              QTableWidgetItem, QHeaderView, QHBoxLayout,
                              QWidget, QSizePolicy, QDateEdit, QCalendarWidget,
                              QComboBox, QCompleter, QAbstractItemView, QScroller)
from PyQt6.QtCore import Qt, QSize, QDate
from PyQt6.QtGui import QColor, QFont


class FilterComboBox(QComboBox):
    """
    Editable combo box with type-to-filter. As you type, the dropdown narrows to
    items that *contain* what you typed (case-insensitive), so you can find a
    category/subcategory/class without scrolling a huge list.

    Populate it with add_option(text, data). current_data() resolves the typed /
    selected text back to its stored data ("" when nothing matches), and
    select_by_data() restores a selection.
    """
    def __init__(self, parent=None, placeholder: str = "Type to search…"):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(15)
        # Start blank with a placeholder rather than a pre-selected first item.
        self.setCurrentIndex(-1)
        self.lineEdit().setPlaceholderText(placeholder)
        # filter against the full item list, matching anywhere in the text
        completer = QCompleter(self.model(), self)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompleter(completer)

    def add_option(self, text: str, data) -> None:
        self.addItem(text, data)

    def current_data(self):
        """Stored data for the currently shown text, or '' if it matches nothing."""
        text = self.currentText().strip()
        if not text:
            return ""
        idx = self.findText(text, Qt.MatchFlag.MatchFixedString)
        return self.itemData(idx) if idx >= 0 else ""

    def select_by_data(self, data) -> None:
        if data in (None, ""):
            self.setCurrentIndex(-1)
            self.clearEditText()
            return
        for i in range(self.count()):
            if self.itemData(i) == data:
                self.setCurrentIndex(i)
                return
        self.setCurrentIndex(-1)
        self.clearEditText()


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

        # Smooth, fine-grained scrolling for the mouse wheel, and finger-drag
        # (kinetic) scrolling for touchscreens — so long lists don't force you
        # to grab the thin scrollbar on the right.
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        QScroller.grabGesture(self.viewport(),
                              QScroller.ScrollerGestureType.TouchGesture)

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


class DateField(QWidget):
    """
    A date box paired with a clearly visible 📅 calendar button. Click the
    button to open a day / month / year picker; the chosen day fills the field.
    You can also still type or arrow through the date directly in the box.

    Exposes date() / setDate() so it drops in wherever a QDateEdit was used.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._edit = QDateEdit()
        self._edit.setDisplayFormat("yyyy-MM-dd")
        self._edit.setButtonSymbols(QDateEdit.ButtonSymbols.NoButtons)
        self._edit.setMinimumWidth(108)
        lay.addWidget(self._edit)

        self._btn = QPushButton("📅")
        self._btn.setObjectName("CalendarButton")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFixedWidth(36)
        self._btn.setToolTip("Pick a date")
        self._btn.clicked.connect(self._open_calendar)
        lay.addWidget(self._btn)

        self._cal = QCalendarWidget()
        self._cal.setWindowFlags(Qt.WindowType.Popup)
        self._cal.setObjectName("PopupCalendar")
        self._cal.setGridVisible(True)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self._cal.clicked.connect(self._on_pick)

    def _open_calendar(self):
        self._cal.setSelectedDate(self._edit.date())
        self._cal.move(self._btn.mapToGlobal(self._btn.rect().bottomLeft()))
        self._cal.show()

    def _on_pick(self, date: QDate):
        self._edit.setDate(date)
        self._cal.hide()

    # ── QDateEdit-compatible API ──
    def date(self) -> QDate:
        return self._edit.date()

    def setDate(self, d: QDate):
        self._edit.setDate(d)


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
