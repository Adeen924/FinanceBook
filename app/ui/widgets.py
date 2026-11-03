"""Shared reusable widgets."""
from PyQt6.QtWidgets import (QPushButton, QLabel, QFrame, QTableWidget,
                              QTableWidgetItem, QHeaderView, QHBoxLayout,
                              QWidget, QSizePolicy, QLineEdit, QCalendarWidget,
                              QComboBox, QCompleter, QAbstractItemView, QScroller,
                              QToolTip)
from PyQt6.QtCore import Qt, QSize, QDate, QRectF, QPointF, QLineF, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen, QBrush,
                         QIcon, QPixmap, QLinearGradient, QRadialGradient,
                         QFontMetrics, QTextCharFormat)

from ui import styles as S


# ── Line icons ───────────────────────────────────────────────────────────────
# Simple stroke icons painted with QPainter so they can be tinted per state
# (emoji can't change colour, and the frozen build has no SVG dependency).

def _draw_icon(p: QPainter, name: str, color: QColor):
    """Draw icon `name` on a 24×24 unit grid (caller scales the painter)."""
    def pts(*xy):
        return [QPointF(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)]

    if name == "dashboard":
        for x, y in ((3.5, 3.5), (13, 3.5), (3.5, 13), (13, 13)):
            p.drawRoundedRect(QRectF(x, y, 7.5, 7.5), 2.2, 2.2)
    elif name == "accounts":
        p.drawPolygon(pts(3.5, 9.5, 12, 4, 20.5, 9.5))
        for x in (6.5, 10.2, 13.8, 17.5):
            p.drawLine(QPointF(x, 12), QPointF(x, 17))
        p.drawLine(QPointF(3.5, 20), QPointF(20.5, 20))
    elif name == "loans":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawLine(QPointF(9, 15), QPointF(15, 9))
        p.drawEllipse(QPointF(9.2, 9.2), 1.2, 1.2)
        p.drawEllipse(QPointF(14.8, 14.8), 1.2, 1.2)
    elif name == "transactions":
        for y in (6.5, 12, 17.5):
            p.drawLine(QPointF(9, y), QPointF(20.5, y))
            p.drawPoint(QPointF(4.5, y))
    elif name == "transfers":
        p.drawLine(QPointF(4, 8), QPointF(19, 8))
        p.drawPolyline(pts(15.5, 4.5, 19, 8, 15.5, 11.5))
        p.drawLine(QPointF(20, 16), QPointF(5, 16))
        p.drawPolyline(pts(8.5, 12.5, 5, 16, 8.5, 19.5))
    elif name == "import":
        p.drawPolyline(pts(4, 14, 4, 19.5, 20, 19.5, 20, 14))
        p.drawLine(QPointF(12, 4), QPointF(12, 14.5))
        p.drawPolyline(pts(8, 10.5, 12, 14.5, 16, 10.5))
    elif name == "rules":
        p.drawLine(QPointF(4, 7.5), QPointF(6.6, 7.5))
        p.drawLine(QPointF(11.4, 7.5), QPointF(20, 7.5))
        p.drawEllipse(QPointF(9, 7.5), 2.4, 2.4)
        p.drawLine(QPointF(4, 16.5), QPointF(12.6, 16.5))
        p.drawLine(QPointF(17.4, 16.5), QPointF(20, 16.5))
        p.drawEllipse(QPointF(15, 16.5), 2.4, 2.4)
    elif name == "categories":
        path = QPainterPath(QPointF(12, 3.5))
        for x, y in ((20.5, 3.5), (20.5, 12), (12, 20.5), (3.5, 12)):
            path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(16, 8), 1.4, 1.4)
    elif name == "reconcile":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawPolyline(pts(8, 12.3, 10.8, 15, 16, 9.3))
    elif name == "reports":
        for x, y in ((4, 12), (10, 7), (16, 3.5)):
            p.drawRoundedRect(QRectF(x, y, 4, 20.5 - y), 1.2, 1.2)
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 6.3, 6.3)
        p.drawEllipse(QPointF(12, 12), 2.4, 2.4)
        p.save()
        p.translate(12, 12)
        p.setBrush(color)
        for _ in range(8):
            p.drawRoundedRect(QRectF(-1.5, -10, 3, 3), 0.8, 0.8)
            p.rotate(45)
        p.restore()
    elif name == "refresh":
        p.drawArc(QRectF(4.5, 4.5, 15, 15), 80 * 16, 280 * 16)
        p.drawPolyline(pts(16.8, 13.2, 19.5, 10.5, 22, 13.4))
    elif name == "calendar":
        p.drawRoundedRect(QRectF(4, 5.5, 16, 14.5), 2.5, 2.5)
        p.drawLine(QPointF(4, 10), QPointF(20, 10))
        p.drawLine(QPointF(8.5, 3.5), QPointF(8.5, 7))
        p.drawLine(QPointF(15.5, 3.5), QPointF(15.5, 7))
    elif name == "wallet":
        p.drawRoundedRect(QRectF(3.5, 6, 17, 13), 3, 3)
        p.drawLine(QPointF(6, 6), QPointF(15.5, 3.5))
        p.drawEllipse(QPointF(16, 12.5), 1.2, 1.2)
    elif name == "trend_up":
        p.drawPolyline(pts(3.5, 17, 9.5, 11, 13, 14.5, 20.5, 7))
        p.drawPolyline(pts(15.5, 7, 20.5, 7, 20.5, 12))
    elif name == "card":
        p.drawRoundedRect(QRectF(3, 5.5, 18, 13), 2.5, 2.5)
        p.drawLine(QPointF(3, 10), QPointF(21, 10))
        p.drawLine(QPointF(6.5, 14.8), QPointF(10, 14.8))
    elif name == "flow":
        p.drawLine(QPointF(8, 19), QPointF(8, 5))
        p.drawPolyline(pts(4.5, 8.5, 8, 5, 11.5, 8.5))
        p.drawLine(QPointF(16, 5), QPointF(16, 19))
        p.drawPolyline(pts(12.5, 15.5, 16, 19, 19.5, 15.5))
    elif name == "arrow_right":
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
        p.drawPolyline(pts(13.5, 6.5, 19, 12, 13.5, 17.5))
    elif name == "chev_left":
        p.drawPolyline(pts(14.5, 6, 8.5, 12, 14.5, 18))
    elif name == "chev_right":
        p.drawPolyline(pts(9.5, 6, 15.5, 12, 9.5, 18))
    elif name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))


def icon_pixmap(name: str, color: str, size: int = 18, stroke: float = 1.8) -> QPixmap:
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    pen = QPen(c, stroke * 24 / size)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.scale(size / 24, size / 24)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw_icon(p, name, c)
    p.end()
    return pm


def line_icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))


def brand_pixmap(size: int = 32) -> QPixmap:
    """App mark: blue gradient tile with three rising bars."""
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0, 0, size, size)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(S.PRIMARY))
    g.setColorAt(1, QColor(S.PRIMARY_2))
    path = QPainterPath()
    path.addRoundedRect(r, size * 0.3, size * 0.3)
    p.fillPath(path, g)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    u = size / 32
    for i, h in enumerate((8, 13, 18)):
        p.drawRoundedRect(QRectF((8 + i * 6) * u, (24 - h) * u, 4 * u, h * u), 1.6 * u, 1.6 * u)
    p.end()
    return pm


class IconBubble(QLabel):
    """Round tinted badge holding a line icon (used on KPI cards)."""
    def __init__(self, icon: str, color: str = S.PRIMARY, size: int = 36,
                 on_accent: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fg = "#ffffff" if on_accent else color
        self.setPixmap(icon_pixmap(icon, fg, int(size * 0.5)))
        c = QColor(color)
        bg = ("rgba(255,255,255,0.22)" if on_accent
              else f"rgba({c.red()},{c.green()},{c.blue()},0.12)")
        self.setStyleSheet(f"background:{bg}; border-radius:{size // 2}px;")


# ── Inputs ───────────────────────────────────────────────────────────────────

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
    """Sidebar item: line icon + label; active state is a soft blue pill."""
    def __init__(self, text, icon="", parent=None):
        super().__init__(f"  {text}", parent)
        self._icon_name = icon
        self._active = False
        self.setCheckable(False)
        self.setProperty("active", False)
        self.setMinimumHeight(40)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_icon()

    def _refresh_icon(self, hover: bool = False):
        if not self._icon_name:
            return
        color = S.PRIMARY if self._active else (S.TEXT if hover else S.MUTED)
        self.setIcon(line_icon(self._icon_name, color, 18))

    def set_active(self, active: bool):
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_icon()

    def enterEvent(self, event):
        self._refresh_icon(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._refresh_icon()
        super().leaveEvent(event)


# ── Glass surfaces ───────────────────────────────────────────────────────────

class GlassCard(QFrame):
    """
    Translucent rounded card: white gradient fill, white inner edge plus a faint
    blue outer edge, a top-left highlight and a soft blue-grey shadow.

    The shadow is painted by the card itself inside SHADOW_MARGINS (reserved via
    contentsMargins, so layouts never overlap it) instead of using
    QGraphicsDropShadowEffect, which re-blurs the whole card — tables included —
    on every repaint and makes scrolling sluggish.
    """
    SHADOW_MARGINS = (10, 6, 10, 14)

    def __init__(self, parent=None, radius: int = S.RADIUS_CARD, accent: bool = False,
                 hoverable: bool = False, shadow: bool = True):
        super().__init__(parent)
        self._radius = radius
        self._accent = accent
        self._hoverable = hoverable
        self._hovered = False
        self._shadow = shadow
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._margins = self.SHADOW_MARGINS if shadow else (1, 1, 1, 1)
        self.setContentsMargins(*self._margins)
        if hoverable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def body_rect(self) -> QRectF:
        l, t, r, b = self._margins
        return QRectF(self.rect()).adjusted(l, t, -r, -b)

    def enterEvent(self, event):
        if self._hoverable:
            self._hovered = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hoverable:
            self._hovered = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = self.body_rect()
        rad = self._radius
        path = QPainterPath()
        path.addRoundedRect(body, rad, rad)

        if self._shadow:
            clip = QPainterPath()
            clip.addRect(QRectF(self.rect()))
            p.save()
            p.setClipPath(clip.subtracted(path))
            alpha = 8 if self._hovered else 5
            if self._accent:
                alpha += 4
            for i in range(1, 10):
                ring = QPainterPath()
                ring.addRoundedRect(body.adjusted(-i * 1.0, -i * 0.5 + 2, i * 1.0, i * 1.2 + 2),
                                    rad + i, rad + i)
                p.fillPath(ring, QColor(38, 70, 150, alpha))
            p.restore()

        if self._accent:
            g = QLinearGradient(body.topLeft(), body.bottomRight())
            g.setColorAt(0, QColor(S.PRIMARY))
            g.setColorAt(1, QColor(S.PRIMARY_2))
        else:
            hi = 26 if self._hovered else 0
            g = QLinearGradient(body.topLeft(), body.bottomRight())
            g.setColorAt(0, QColor(255, 255, 255, 185 + hi))
            g.setColorAt(1, QColor(255, 255, 255, 135 + hi))
        p.fillPath(path, g)

        p.save()
        p.setClipPath(path)
        if self._accent:
            # Decorative translucent rings in the corner of the hero card.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawEllipse(QPointF(body.right() - 20, body.top() + 10), 70, 70)
            p.setBrush(QColor(255, 255, 255, 18))
            p.drawEllipse(QPointF(body.right() + 10, body.bottom()), 80, 80)
        hl = QRadialGradient(QPointF(body.left() + body.width() * 0.12, body.top()),
                             max(body.width(), body.height()) * 0.8)
        hl.setColorAt(0, QColor(255, 255, 255, 60 if self._accent else 120))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(body, hl)
        p.restore()

        outer = QPainterPath()
        outer.addRoundedRect(body.adjusted(-0.5, -0.5, 0.5, 0.5), rad + 0.5, rad + 0.5)
        p.setPen(QPen(QColor(47, 107, 255, 34), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(outer)
        inner = QPainterPath()
        inner.addRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), rad - 0.5, rad - 0.5)
        p.setPen(QPen(QColor(255, 255, 255, 90 if self._accent else 225), 1))
        p.drawPath(inner)
        p.end()


class Card(GlassCard):
    """Backwards-compatible name for a plain glass card."""


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


class LinkButton(QPushButton):
    """Borderless blue text button ("View all →")."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Link")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class FitLabel(QLabel):
    """Single-line label that shrinks its font to fit the available width."""
    def __init__(self, text="", max_px: int = 28, min_px: int = 15, css: str = "",
                 parent=None):
        super().__init__(text, parent)
        self._max_px = max_px
        self._min_px = min_px
        self._css = css
        self._px = None
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text):
        super().setText(text)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        f = QFont(self.font())
        px = self._max_px
        avail = max(self.width() - 2, 20)
        while px > self._min_px:
            f.setPixelSize(px)
            if QFontMetrics(f).horizontalAdvance(self.text()) <= avail:
                break
            px -= 1
        # Size goes through the stylesheet: the app-wide QSS font-size would
        # otherwise override a plain setFont().
        if px != self._px:
            self._px = px
            self.setStyleSheet(f"{self._css} font-size:{px}px;")

    def sizeHint(self):
        f = QFont(self.font())
        f.setPixelSize(self._max_px)
        return QSize(80, QFontMetrics(f).height() + 2)


class DeltaChip(QLabel):
    """Small green / red pill showing a change (e.g. "▲ 4.2%")."""
    def __init__(self, parent=None, on_accent: bool = False):
        super().__init__(parent)
        self._on_accent = on_accent
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_delta(self, text: str, good: bool | None):
        self.setText(text)
        if self._on_accent:
            bg, fg = "rgba(255,255,255,0.22)", "#ffffff"
        elif good is None:
            bg, fg = "rgba(107,122,153,0.12)", S.MUTED
        elif good:
            bg, fg = "rgba(22,163,74,0.12)", "#15803d"
        else:
            bg, fg = "rgba(229,72,77,0.12)", "#c9363b"
        self.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:9px; "
                           "padding:2px 8px; font-size:11px; font-weight:600;")


class DataTable(QTableWidget):
    """Standard table with consistent styling and helpers."""
    def __init__(self, columns: list[str], parent=None, flat: bool = False):
        super().__init__(parent)
        if flat:
            self.setObjectName("Flat")
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels([c.upper() for c in columns])
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setFrameShape(QFrame.Shape.NoFrame if flat else QFrame.Shape.StyledPanel)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hf = QFont(hdr.font())
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        hdr.setFont(hf)
        self.setWordWrap(False)
        self.setMouseTracking(True)

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


# ── Charts (QPainter only) ───────────────────────────────────────────────────

def compact_money(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 1_000:
        return f"{sign}${a / 1_000:.1f}k".replace(".0k", "k")
    return f"{sign}${a:,.0f}"


def _nice_ceiling(v: float) -> float:
    if v <= 0:
        return 100.0
    p = 1.0
    while p * 10 <= v:
        p *= 10
    for f in (1, 2, 2.5, 5, 10):
        if f * p >= v:
            return f * p
    return 10 * p


class SmoothLineChart(QWidget):
    """
    Smooth multi-series line/area chart. The first series gets a gradient area
    fill. Hovering shows a guide line and a tooltip with every series' value.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels: list[str] = []
        self._series: list[dict] = []
        self._hover = -1
        self._empty_text = "No data for this period yet"
        self.setMouseTracking(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, labels: list[str], series: list[dict], empty_text: str = None):
        """series: [{"name": str, "color": "#hex", "values": [float, …]}, …]"""
        self._labels = labels
        self._series = series
        if empty_text:
            self._empty_text = empty_text
        self._hover = -1
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(52, 12, -12, -30)

    def _points(self, values, plot: QRectF, top: float) -> list[QPointF]:
        n = len(values)
        out = []
        for i, v in enumerate(values):
            x = plot.center().x() if n == 1 else plot.left() + plot.width() * i / (n - 1)
            y = plot.bottom() - (max(v, 0) / top) * plot.height()
            out.append(QPointF(x, y))
        return out

    @staticmethod
    def _smooth_path(pts: list[QPointF], plot: QRectF) -> QPainterPath:
        path = QPainterPath(pts[0])
        if len(pts) == 1:
            return path

        def clamp(pt):
            return QPointF(pt.x(), min(max(pt.y(), plot.top()), plot.bottom()))

        for i in range(len(pts) - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < len(pts) else p2
            c1 = clamp(p1 + (p2 - p0) / 6)
            c2 = clamp(p2 - (p3 - p1) / 6)
            path.cubicTo(c1, c2, p2)
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        all_vals = [v for s in self._series for v in s["values"]]
        top = _nice_ceiling(max(all_vals) if all_vals else 0)

        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)

        # gridlines + y labels
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            pen = QPen(QColor(15, 27, 61, 22 if i else 40), 1)
            if i:
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setDashPattern([3, 4])
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(S.SUBTLE))
            p.drawText(QRectF(0, y - 8, plot.left() - 10, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       compact_money(top * i / 4))

        n = len(self._labels)
        if n:
            step = max(1, (n + 7) // 8)
            for i, lab in enumerate(self._labels):
                if i % step and i != n - 1:
                    continue
                x = plot.center().x() if n == 1 else plot.left() + plot.width() * i / (n - 1)
                p.setPen(QColor(S.TEXT if i == self._hover else S.SUBTLE))
                p.drawText(QRectF(x - 30, plot.bottom() + 8, 60, 16),
                           Qt.AlignmentFlag.AlignCenter, lab)

        if not all_vals or max(all_vals) <= 0:
            p.setPen(QColor(S.MUTED))
            f = QFont(self.font())
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, self._empty_text)
            p.end()
            return

        # series (draw the filled one last so it sits on top)
        for idx in reversed(range(len(self._series))):
            s = self._series[idx]
            color = QColor(s["color"])
            pts = self._points(s["values"], plot, top)
            line = self._smooth_path(pts, plot)
            area = QPainterPath(line)
            area.lineTo(pts[-1].x(), plot.bottom())
            area.lineTo(pts[0].x(), plot.bottom())
            area.closeSubpath()
            g = QLinearGradient(0, plot.top(), 0, plot.bottom())
            c0 = QColor(color)
            c0.setAlpha(80 if idx == 0 else 34)
            c1 = QColor(color)
            c1.setAlpha(0)
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.fillPath(area, g)
            pen = QPen(color, 2.6 if idx == 0 else 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)

        if 0 <= self._hover < n:
            pts_by_series = [self._points(s["values"], plot, top) for s in self._series]
            x = pts_by_series[0][self._hover].x()
            guide = QPen(QColor(47, 107, 255, 70), 1)
            guide.setStyle(Qt.PenStyle.DashLine)
            p.setPen(guide)
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            for s, pts in zip(self._series, pts_by_series):
                pt = pts[self._hover]
                p.setPen(Qt.PenStyle.NoPen)
                halo = QColor(s["color"])
                halo.setAlpha(45)
                p.setBrush(halo)
                p.drawEllipse(pt, 8, 8)
                p.setPen(QPen(QColor(s["color"]), 2.4))
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(pt, 4.2, 4.2)
            self._draw_tooltip(p, plot, x)
        p.end()

    def _draw_tooltip(self, p: QPainter, plot: QRectF, x: float):
        f = QFont(self.font())
        f.setPixelSize(12)
        bold = QFont(f)
        bold.setBold(True)
        fm, fmb = QFontMetrics(f), QFontMetrics(bold)
        rows = [(s["name"], s["color"], s["values"][self._hover]) for s in self._series]
        title = self._labels[self._hover]
        width = max([fmb.horizontalAdvance(title)] +
                    [fm.horizontalAdvance(n) + fmb.horizontalAdvance(f"${v:,.2f}") + 40
                     for n, _, v in rows]) + 24
        height = 30 + 20 * len(rows)
        left = x + 14 if x + 14 + width < plot.right() else x - 14 - width
        box = QRectF(left, plot.top() + 6, width, height)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        shadow = QPainterPath()
        shadow.addRoundedRect(box.translated(0, 3).adjusted(-1, -1, 1, 1), 11, 11)
        p.fillPath(shadow, QColor(38, 70, 150, 22))
        p.fillPath(path, QColor(255, 255, 255, 245))
        p.setPen(QPen(QColor(47, 107, 255, 50), 1))
        p.drawPath(path)
        p.setFont(bold)
        p.setPen(QColor(S.TEXT))
        p.drawText(QRectF(box.left() + 12, box.top() + 8, width - 24, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        y = box.top() + 30
        for name, color, v in rows:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(box.left() + 16, y + 7), 4, 4)
            p.setFont(f)
            p.setPen(QColor(S.MUTED))
            p.drawText(QRectF(box.left() + 26, y, width - 38, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(bold)
            p.setPen(QColor(S.TEXT))
            p.drawText(QRectF(box.left() + 26, y, width - 38, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"${v:,.2f}")
            y += 20

    def mouseMoveEvent(self, event):
        n = len(self._labels)
        if n == 0:
            return
        plot = self._plot_rect()
        x = event.position().x()
        idx = 0 if n == 1 else round((x - plot.left()) / plot.width() * (n - 1))
        idx = min(max(idx, 0), n - 1)
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        super().leaveEvent(event)


class DonutChart(QWidget):
    """Ring chart with a centred total; hovering a slice shows its share."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[tuple] = []
        self._center_value = ""
        self._center_label = ""
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumSize(150, 150)

    def set_data(self, segments: list[tuple], center_value: str, center_label: str):
        """segments: [(label, value, "#hex"), …] — values must be positive."""
        self._segments = [s for s in segments if s[1] > 0]
        self._center_value = center_value
        self._center_label = center_label
        self._hover = -1
        self.update()

    def _ring_rect(self) -> QRectF:
        side = min(self.width(), self.height()) - 12
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._ring_rect()
        thick = max(rect.width() * 0.15, 12)
        ring = rect.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2)
        total = sum(v for _, v, _ in self._segments)

        track = QPen(QColor(47, 107, 255, 22), thick)
        p.setPen(track)
        p.drawEllipse(ring)

        if total > 0:
            gap = 2.0 if len(self._segments) > 1 else 0.0
            start = 90.0
            for i, (_, v, color) in enumerate(self._segments):
                span = 360.0 * v / total
                draw_span = max(span - gap, 0.6)
                pen = QPen(QColor(color), thick + (5 if i == self._hover else 0))
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.drawArc(ring, int((start - gap / 2) * 16), int(-draw_span * 16))
                start -= span

        value, label = self._center_value, self._center_label
        if 0 <= self._hover < len(self._segments) and total > 0:
            name, v, _ = self._segments[self._hover]
            value = f"{v / total * 100:.0f}%"
            label = name
        inner = ring.adjusted(thick, thick, -thick, -thick)
        vf = QFont(self.font())
        vf.setPixelSize(max(int(rect.width() * 0.11), 14))
        vf.setBold(True)
        lf = QFont(self.font())
        lf.setPixelSize(11)
        p.setPen(QColor(S.TEXT))
        p.setFont(vf)
        vh = QFontMetrics(vf).height()
        p.drawText(QRectF(inner.left(), inner.center().y() - vh + 4, inner.width(), vh),
                   Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(vf).elidedText(value, Qt.TextElideMode.ElideRight, int(inner.width())))
        p.setPen(QColor(S.MUTED))
        p.setFont(lf)
        p.drawText(QRectF(inner.left(), inner.center().y() + 6, inner.width(), 16),
                   Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(lf).elidedText(label, Qt.TextElideMode.ElideRight, int(inner.width())))
        p.end()

    def mouseMoveEvent(self, event):
        rect = self._ring_rect()
        c = rect.center()
        pos = event.position()
        dist = QLineF(c, pos).length()
        outer = rect.width() / 2
        thick = max(rect.width() * 0.15, 12)
        idx = -1
        total = sum(v for _, v, _ in self._segments)
        if total > 0 and outer - thick - 4 <= dist <= outer + 4:
            # QLineF.angle(): 0° at 3 o'clock, counter-clockwise. Slices start at
            # 12 o'clock and run clockwise.
            ang = (90.0 - QLineF(c, pos).angle()) % 360.0
            acc = 0.0
            for i, (_, v, _) in enumerate(self._segments):
                acc += 360.0 * v / total
                if ang <= acc:
                    idx = i
                    break
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        super().leaveEvent(event)


# ── Date entry ───────────────────────────────────────────────────────────────

class DateField(QWidget):
    """
    A plain free-text date box paired with a clearly visible calendar button.

    Unlike a segmented QDateEdit, this is an ordinary text box: you can select
    everything, delete it, and retype the whole date — month, day and year —
    with no invisible separators trapping the cursor between sections. Click the
    calendar button to pick a day instead. Typing is forgiving: ``08/04/2026``,
    ``8/4/2026``, ``08-04-2026`` and even ISO ``2026-08-04`` are all accepted,
    and the box tidies itself up to ``MM/DD/YYYY`` when you leave it.

    Exposes date() / setDate() and a dateChanged signal so it drops in wherever
    a QDateEdit was used.
    """
    dateChanged = pyqtSignal(QDate)

    # Formats we try when parsing whatever the user typed. Month-first
    # (MM/DD/YYYY) is the canonical display form and the preferred parse order
    # (US usage). ISO (yyyy-MM-dd) and other forms stay accepted so dates loaded
    # from the database — stored as yyyy-MM-dd — still parse cleanly.
    _PARSE_FORMATS = ("MM/dd/yyyy", "M/d/yyyy",
                      "MM-dd-yyyy", "M-d-yyyy",
                      "yyyy-MM-dd", "yyyy/MM/dd", "yyyy.MM.dd")
    _DISPLAY_FORMAT = "MM/dd/yyyy"

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Last known-good date. date() falls back to this when the box holds a
        # half-typed or unparseable value, so callers always get a valid date.
        self._date = QDate.currentDate()

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("MM/DD/YYYY")
        self._edit.setMinimumWidth(108)
        self._edit.setText(self._date.toString(self._DISPLAY_FORMAT))
        # While typing, update (and announce) the date the moment the text
        # forms a valid one; a partial/invalid entry simply keeps the last one.
        self._edit.textEdited.connect(self._on_text_edited)
        # On leaving the box, snap the text back to the canonical YYYY-MM-DD of
        # the last valid date so it never shows garbage or a half-typed value.
        self._edit.editingFinished.connect(self._normalize_text)
        lay.addWidget(self._edit)

        self._btn = QPushButton()
        self._btn.setObjectName("CalendarButton")
        self._btn.setIcon(line_icon("calendar", S.PRIMARY, 18))
        self._btn.setIconSize(QSize(18, 18))
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFixedWidth(36)
        self._btn.setToolTip("Pick a date")
        self._btn.clicked.connect(self._open_calendar)
        lay.addWidget(self._btn)

        self._cal = QCalendarWidget()
        self._cal.setWindowFlags(Qt.WindowType.Popup)
        self._cal.setObjectName("PopupCalendar")
        self._cal.setGridVisible(False)
        self._cal.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self._cal.clicked.connect(self._on_pick)
        weekend = QTextCharFormat()
        weekend.setForeground(QColor(S.DANGER))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self._cal.setWeekdayTextFormat(day, weekend)
        for btn_name, icon in (("qt_calendar_prevmonth", "chev_left"),
                               ("qt_calendar_nextmonth", "chev_right")):
            btn = self._cal.findChild(QWidget, btn_name)
            if btn is not None:
                btn.setIcon(line_icon(icon, "#ffffff", 18))

    def _parse(self, text: str) -> QDate:
        """Best-effort parse of free-typed text into a QDate (invalid if none)."""
        text = text.strip()
        for fmt in self._PARSE_FORMATS:
            d = QDate.fromString(text, fmt)
            if d.isValid():
                return d
        return QDate()

    def _on_text_edited(self, text: str):
        d = self._parse(text)
        if d.isValid():
            self._date = d
            self.dateChanged.emit(d)

    def _normalize_text(self):
        # Re-parse in case editing finished on a valid non-canonical form
        # (e.g. "8/4/2026"), then rewrite in the canonical display format.
        d = self._parse(self._edit.text())
        if d.isValid():
            self._date = d
        self._edit.setText(self._date.toString(self._DISPLAY_FORMAT))

    def _open_calendar(self):
        self._cal.setSelectedDate(self.date())
        self._cal.move(self._btn.mapToGlobal(self._btn.rect().bottomLeft()))
        self._cal.show()

    def _on_pick(self, date: QDate):
        self.setDate(date)
        self._cal.hide()

    # ── QDateEdit-compatible API ──
    def date(self) -> QDate:
        d = self._parse(self._edit.text())
        return d if d.isValid() else self._date

    def setDate(self, d: QDate):
        if isinstance(d, QDate) and d.isValid():
            self._date = d
            self._edit.setText(d.toString(self._DISPLAY_FORMAT))
            self.dateChanged.emit(d)


def fmt_date(iso: str) -> str:
    """Format a stored ISO date (yyyy-MM-dd) for display as MM/DD/YYYY.

    Dates are stored canonically as yyyy-MM-dd but shown to the user month-first
    to match the data-entry fields (see DateField). Returns the input unchanged
    if it isn't a valid ISO date (e.g. "Never", "" or an already-formatted date).
    """
    d = QDate.fromString(iso or "", "yyyy-MM-dd")
    return d.toString("MM/dd/yyyy") if d.isValid() else (iso or "")


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
