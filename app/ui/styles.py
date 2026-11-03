"""Design tokens + global stylesheet for the light "glass dashboard" theme.

Everything visual hangs off the tokens at the top of this file. Painted widgets
(GlassCard, charts, the window background) read the same tokens, so changing a
colour here changes it everywhere.
"""
import os
import tempfile

# ── Design tokens ────────────────────────────────────────────────────────────
PRIMARY        = "#2f6bff"
PRIMARY_2      = "#5b9bff"          # gradient end for buttons / active states
PRIMARY_DARK   = "#2457d6"
TEXT           = "#0f1b3d"
TEXT_SOFT      = "#3a4766"
MUTED          = "#6b7a99"
SUBTLE         = "#9aa6bf"
SUCCESS        = "#16a34a"
DANGER         = "#e5484d"
WARNING        = "#f59e0b"
LAVENDER       = "#8b7bff"          # secondary chart series

BG_TOP         = "#eef4ff"
BG_MID         = "#f8fbff"
BG_BOTTOM      = "#e6f0ff"
BLOB_SKY       = (147, 197, 253)    # rgb, painted as soft radial blobs
BLOB_BLUE      = (165, 190, 255)
BLOB_LAVENDER  = (199, 186, 255)

GLASS_FILL     = "rgba(255, 255, 255, 0.62)"
GLASS_STRONG   = "rgba(255, 255, 255, 0.80)"
GLASS_EDGE     = "rgba(255, 255, 255, 0.90)"
BLUE_EDGE      = "rgba(47, 107, 255, 0.14)"
BLUE_EDGE_HI   = "rgba(47, 107, 255, 0.32)"
HOVER          = "rgba(47, 107, 255, 0.06)"
SELECTED       = "rgba(47, 107, 255, 0.13)"
DIVIDER        = "rgba(15, 27, 61, 0.07)"

RADIUS_CARD    = 20
RADIUS_PANEL   = 16
RADIUS_INPUT   = 10
GUTTER         = 20

FONT_STACK     = '"Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", sans-serif'
FONT_DISPLAY   = '"Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", sans-serif'

# ── Legacy names other modules import ────────────────────────────────────────
ACCENT       = PRIMARY
SIDEBAR_BG   = "#ffffff"
SIDEBAR_SEL  = SELECTED
BG           = BG_MID
CARD_BG      = "#ffffff"
BORDER       = "#dbe5f7"


def money(amount: float, cents: bool = True) -> str:
    """$1,234.56 / ($1,234.56) — the app-wide accounting format."""
    if abs(amount) < 0.005:
        amount = 0.0
    fmt = "${:,.2f}" if cents else "${:,.0f}"
    return fmt.format(amount) if amount >= 0 else "(" + fmt.format(abs(amount)) + ")"


def _grad(a: str, b: str) -> str:
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b})"


# ── Small generated images used by the stylesheet (arrows, checkmark) ───────
# Rendered once at startup into a temp folder, so the theme needs no image files
# or SVG plugin in the frozen build.
_ICON_DIR = os.path.join(tempfile.gettempdir(), "FinanceBook_theme")


def _render_theme_icons() -> dict:
    from PyQt6.QtGui import QImage, QPainter, QPen, QColor, QPainterPath
    from PyQt6.QtCore import Qt, QPointF

    os.makedirs(_ICON_DIR, exist_ok=True)
    out = {}

    def draw(name, size, painter_fn):
        path = os.path.join(_ICON_DIR, f"{name}.png")
        img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(2, 2)
        painter_fn(p)
        p.end()
        img.save(path)
        out[name] = path.replace("\\", "/")

    def chevron(color, up=False):
        def fn(p):
            pen = QPen(QColor(color), 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            pts = [QPointF(2.5, 4), QPointF(6, 7.5), QPointF(9.5, 4)]
            if up:
                pts = [QPointF(x.x(), 11.5 - x.y()) for x in pts]
            p.drawPolyline(pts)
        return fn

    def check(p):
        pen = QPen(QColor("#ffffff"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(QPointF(3.2, 7.4))
        path.lineTo(5.9, 10.0)
        path.lineTo(10.8, 4.3)
        p.drawPath(path)

    def dot(p):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(7, 7), 3, 3)

    draw("chevron_down", 12, chevron(MUTED))
    draw("chevron_up", 12, chevron(MUTED, up=True))
    draw("chevron_down_white", 12, chevron("#ffffff"))
    draw("check", 14, check)
    draw("dot", 14, dot)
    return out


def build_qss(icons: dict | None = None) -> str:
    icons = icons or {}

    def url(name):
        return f"url({icons[name]})" if name in icons else "none"

    btn_grad       = _grad(PRIMARY, PRIMARY_2)
    btn_grad_hover = _grad("#3d77ff", "#6aa7ff")
    btn_grad_press = _grad(PRIMARY_DARK, "#4a86e8")
    glass_border = (f"border: 1px solid {GLASS_EDGE}; "
                    f"border-right-color: {BLUE_EDGE}; border-bottom-color: {BLUE_EDGE};")

    return f"""
/* ── Global ─────────────────────────────────────────────── */
QWidget {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {TEXT};
}}
QDialog, QMessageBox, QInputDialog, QProgressDialog {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #f3f7ff, stop:0.55 #fbfdff, stop:1 #edf3ff);
}}
QMessageBox QLabel {{ color: {TEXT}; }}
QToolTip {{
    background: #ffffff;
    color: {TEXT};
    border: 1px solid {BLUE_EDGE_HI};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ── Typography ─────────────────────────────────────────── */
QLabel#PageTitle {{
    font-family: {FONT_DISPLAY};
    font-size: 26px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#PageSubtitle {{ color: {MUTED}; font-size: 13px; }}
QLabel#CardTitle {{
    font-family: {FONT_DISPLAY};
    font-size: 15px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#Overline {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#Muted   {{ color: {MUTED}; font-size: 12px; }}
QLabel#Danger  {{ color: {DANGER}; }}
QLabel#Success {{ color: {SUCCESS}; }}
QLabel#Warning {{ color: {WARNING}; }}
QLabel#InfoBox {{
    background: rgba(47, 107, 255, 0.07);
    border: 1px solid rgba(47, 107, 255, 0.18);
    border-radius: 12px;
    padding: 10px 14px;
    color: {TEXT_SOFT};
}}
QLabel#Chip {{
    background: rgba(47, 107, 255, 0.10);
    color: {PRIMARY};
    border-radius: 9px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QFrame#ReadOnlyBanner {{
    background: rgba(245, 158, 11, 0.14);
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 12px;
}}
QFrame#ReadOnlyBanner QLabel {{ color: #92400e; font-size: 12px; background: transparent; }}
QPushButton#ConnectButton {{
    padding: 10px 18px;
    font-weight: 600;
}}
QLabel#WarnChip {{
    background: rgba(245, 158, 11, 0.14);
    color: #b45309;
    border: 1px solid rgba(245, 158, 11, 0.25);
    border-radius: 12px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {DIVIDER};
    background: {DIVIDER};
    border: none;
    max-height: 1px;
}}

/* ── Tables ─────────────────────────────────────────────── */
QTableWidget, QTableView {{
    background: {GLASS_FILL};
    alternate-background-color: rgba(47, 107, 255, 0.025);
    {glass_border}
    border-radius: {RADIUS_PANEL}px;
    padding: 4px;
    gridline-color: {DIVIDER};
    selection-background-color: {SELECTED};
    selection-color: {TEXT};
    outline: 0;
}}
QTableView::item {{
    padding: 2px 10px;
    border: none;
    border-bottom: 1px solid {DIVIDER};
}}
QTableView::item:hover {{ background: {HOVER}; }}
QTableView::item:selected {{ background: {SELECTED}; }}
QTableWidget#Flat, QTableView#Flat {{
    background: transparent;
    border: none;
    padding: 0;
}}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: rgba(47, 107, 255, 0.05);
    color: {MUTED};
    font-size: 11px;
    font-weight: 700;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid rgba(47, 107, 255, 0.12);
}}
QHeaderView::section:first {{ border-top-left-radius: 12px; }}
QHeaderView::section:last {{ border-top-right-radius: 12px; }}
QHeaderView::section:only-one {{ border-top-left-radius: 12px; border-top-right-radius: 12px; }}
QTableWidget#Flat QHeaderView::section {{ background: transparent; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ── Trees / Lists ──────────────────────────────────────── */
QTreeWidget, QTreeView, QListWidget, QListView {{
    background: {GLASS_FILL};
    alternate-background-color: rgba(47, 107, 255, 0.025);
    {glass_border}
    border-radius: {RADIUS_PANEL}px;
    padding: 4px;
    selection-background-color: {SELECTED};
    selection-color: {TEXT};
    outline: 0;
}}
QTreeView::item, QListView::item {{
    padding: 5px 4px;
    border: none;
    border-radius: 6px;
}}
QTreeView::item:hover, QListView::item:hover {{ background: {HOVER}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {SELECTED}; color: {TEXT}; }}
QTreeView::branch {{ background: transparent; }}

/* ── Buttons ────────────────────────────────────────────── */
QPushButton {{
    background: {btn_grad};
    color: #ffffff;
    border: none;
    border-radius: {RADIUS_INPUT}px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover   {{ background: {btn_grad_hover}; }}
QPushButton:pressed {{ background: {btn_grad_press}; }}
QPushButton:disabled {{ background: rgba(107, 122, 153, 0.28); color: rgba(255, 255, 255, 0.92); }}

QPushButton#Secondary {{
    background: {GLASS_STRONG};
    color: {TEXT};
    border: 1px solid {BLUE_EDGE};
}}
QPushButton#Secondary:hover {{ background: #ffffff; color: {PRIMARY}; border-color: {BLUE_EDGE_HI}; }}
QPushButton#Secondary:pressed {{ background: {SELECTED}; }}
QPushButton#Secondary:disabled {{ background: rgba(255, 255, 255, 0.45); color: {SUBTLE}; }}

QPushButton#Danger {{ background: {_grad(DANGER, "#f0787b")}; }}
QPushButton#Danger:hover {{ background: {_grad("#d93d42", "#ec6a6e")}; }}
QPushButton#Success {{ background: {_grad(SUCCESS, "#34c06a")}; }}
QPushButton#Success:hover {{ background: {_grad("#13923f", "#2cb15f")}; }}

QPushButton#Link {{
    background: transparent;
    color: {PRIMARY};
    border: none;
    padding: 4px 6px;
    font-weight: 600;
}}
QPushButton#Link:hover {{ color: {PRIMARY_DARK}; background: {HOVER}; }}

QPushButton#Segment {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-radius: 8px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#Segment:hover {{ color: {TEXT}; }}
QPushButton#Segment:checked {{ background: #ffffff; color: {PRIMARY}; }}
QFrame#SegmentGroup {{
    background: rgba(47, 107, 255, 0.08);
    border-radius: 10px;
}}

QPushButton::menu-indicator {{
    image: {url("chevron_down")};
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 8px;
    width: 10px;
    height: 10px;
}}
QPushButton#Secondary[hasMenu="true"] {{ padding-right: 28px; }}

QFrame#AccountRow {{ background: transparent; border-radius: 12px; }}
QFrame#AccountRow:hover {{ background: {HOVER}; }}

/* In-table action buttons (Edit / Delete / Reconcile …) */
QPushButton#CellButton {{
    background: {GLASS_STRONG};
    color: {TEXT};
    border: 1px solid {BLUE_EDGE};
    border-radius: 8px;
    padding: 4px 12px;
    font-size: 13px;
}}
QPushButton#CellButton:hover {{ background: #ffffff; color: {PRIMARY}; border-color: {BLUE_EDGE_HI}; }}
QPushButton#CellPrimary {{
    background: {btn_grad}; color: #ffffff; border: none;
    border-radius: 8px; padding: 4px 12px; font-size: 13px;
}}
QPushButton#CellPrimary:hover {{ background: {btn_grad_hover}; }}
QPushButton#CellDanger {{
    background: rgba(229, 72, 77, 0.10); color: {DANGER};
    border: 1px solid rgba(229, 72, 77, 0.25);
    border-radius: 8px; padding: 4px 12px; font-size: 13px;
}}
QPushButton#CellDanger:hover {{ background: {DANGER}; color: #ffffff; border-color: {DANGER}; }}
QPushButton#CellSuccess {{
    background: rgba(22, 163, 74, 0.10); color: {SUCCESS};
    border: 1px solid rgba(22, 163, 74, 0.25);
    border-radius: 8px; padding: 4px 12px; font-size: 13px;
}}
QPushButton#CellSuccess:hover {{ background: {SUCCESS}; color: #ffffff; border-color: {SUCCESS}; }}

/* ── Inputs ─────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
    background: rgba(255, 255, 255, 0.82);
    border: 1px solid rgba(47, 107, 255, 0.16);
    border-radius: {RADIUS_INPUT}px;
    padding: 6px 10px;
    min-height: 20px;
    color: {TEXT};
    selection-background-color: rgba(47, 107, 255, 0.24);
    selection-color: {TEXT};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover,
QDoubleSpinBox:hover, QDateEdit:hover, QComboBox:hover {{
    border-color: {BLUE_EDGE_HI};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{
    background: #ffffff;
    border: 2px solid {PRIMARY};
    padding: 5px 9px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QDateEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{
    background: rgba(255, 255, 255, 0.45);
    color: {SUBTLE};
}}
QLineEdit[readOnly="true"] {{ background: rgba(255, 255, 255, 0.55); }}
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0;
    min-height: 0;
}}
QComboBox {{ min-width: 140px; padding-right: 28px; }}
QComboBox:focus {{ padding-right: 27px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
}}
QComboBox::down-arrow {{ image: {url("chevron_down")}; width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: #ffffff;
    border: 1px solid {BLUE_EDGE_HI};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {SELECTED};
    selection-color: {TEXT};
    outline: 0;
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: 6px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button {{
    background: transparent;
    border: none;
    width: 18px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow {{
    image: {url("chevron_up")}; width: 10px; height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow {{
    image: {url("chevron_down")}; width: 10px; height: 10px;
}}
QDateEdit::drop-down {{ border: none; width: 24px; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator, QTableView::indicator, QTreeView::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid #b7c6e6;
    background: rgba(255, 255, 255, 0.9);
}}
QCheckBox::indicator, QTableView::indicator, QTreeView::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QTableView::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked, QTreeView::indicator:checked {{
    background: {btn_grad};
    border-color: {PRIMARY};
    image: {url("check")};
}}
QRadioButton::indicator:checked {{
    background: {btn_grad};
    border-color: {PRIMARY};
    image: {url("dot")};
}}
QCheckBox::indicator:disabled {{ background: rgba(255, 255, 255, 0.4); border-color: #d5deef; }}

/* ── Calendar button + popup ────────────────────────────── */
QPushButton#CalendarButton {{
    background: rgba(255, 255, 255, 0.82);
    color: {TEXT};
    border: 1px solid rgba(47, 107, 255, 0.16);
    border-radius: {RADIUS_INPUT}px;
    padding: 6px 0;
    font-size: 15px;
    min-height: 20px;
}}
QPushButton#CalendarButton:hover {{ background: #ffffff; border-color: {PRIMARY}; }}
QPushButton#CalendarButton:pressed {{ background: {SELECTED}; }}

QCalendarWidget#PopupCalendar {{
    background: #ffffff;
    border: 1px solid {BLUE_EDGE_HI};
}}
QCalendarWidget#PopupCalendar QWidget#qt_calendar_navigationbar {{
    background: {_grad(PRIMARY, PRIMARY_2)};
    min-height: 34px;
}}
QCalendarWidget#PopupCalendar QToolButton {{
    color: #ffffff;
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 13px;
    font-weight: 600;
    icon-size: 16px;
}}
QCalendarWidget#PopupCalendar QToolButton:hover {{ background: rgba(255, 255, 255, 0.22); }}
QCalendarWidget#PopupCalendar QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget#PopupCalendar QMenu {{ color: {TEXT}; }}
QCalendarWidget#PopupCalendar QSpinBox {{
    color: {TEXT};
    background: #ffffff;
    border-radius: 6px;
    selection-background-color: {SELECTED};
}}
QCalendarWidget#PopupCalendar QAbstractItemView:enabled {{
    color: {TEXT};
    background: #ffffff;
    selection-background-color: {PRIMARY};
    selection-color: #ffffff;
    outline: 0;
    border: none;
    border-radius: 0;
    padding: 0;
}}
QCalendarWidget#PopupCalendar QAbstractItemView:disabled {{ color: #c3cbdb; }}
QCalendarWidget QTableView {{
    background: #ffffff;
    border: none;
    border-radius: 0;
    padding: 0;
}}
QCalendarWidget QTableView::item {{ padding: 0; border: none; border-radius: 6px; }}
QCalendarWidget QTableView::item:hover {{ background: {HOVER}; }}
QCalendarWidget QTableView::item:selected {{ background: {PRIMARY}; color: #ffffff; }}

/* ── Menus ──────────────────────────────────────────────── */
QMenu {{
    background: #ffffff;
    border: 1px solid {BLUE_EDGE_HI};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {SELECTED}; color: {PRIMARY}; }}
QMenu::separator {{ height: 1px; background: {DIVIDER}; margin: 4px 8px; }}

/* ── Scroll areas & scrollbars ──────────────────────────── */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget#qt_scrollarea_viewport,
QScrollArea > QWidget > QWidget#ScrollContent {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px 4px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(15, 27, 61, 0.16);
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(47, 107, 255, 0.45); }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(15, 27, 61, 0.16);
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: rgba(47, 107, 255, 0.45); }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}

/* ── Group boxes = glass sections ───────────────────────── */
QGroupBox {{
    background: {GLASS_FILL};
    {glass_border}
    border-radius: {RADIUS_CARD - 2}px;
    margin-top: 0;
    padding: 40px 16px 14px 16px;
    font-weight: 600;
    font-size: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 18px;
    top: 14px;
    padding: 0;
    color: {TEXT};
    background: transparent;
}}
QGroupBox#DangerZone {{
    background: rgba(255, 245, 245, 0.72);
    border: 1px solid rgba(229, 72, 77, 0.30);
}}
QGroupBox#DangerZone::title {{ color: {DANGER}; }}

/* ── Tabs ───────────────────────────────────────────────── */
QTabWidget::pane {{
    background: rgba(255, 255, 255, 0.45);
    {glass_border}
    border-radius: {RADIUS_CARD - 2}px;
    padding: 10px;
    top: 6px;
}}
QTabWidget::tab-bar {{ left: 2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: none;
    padding: 7px 16px;
    margin-right: 6px;
    border-radius: 10px;
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{ background: rgba(255, 255, 255, 0.75); color: {TEXT}; }}
QTabBar::tab:selected {{
    background: #ffffff;
    color: {PRIMARY};
    border: 1px solid {BLUE_EDGE};
}}

/* ── Progress / misc ────────────────────────────────────── */
QProgressBar {{
    background: rgba(47, 107, 255, 0.10);
    border: none;
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
    min-height: 12px;
    font-size: 11px;
}}
QProgressBar::chunk {{ background: {btn_grad}; border-radius: 6px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {SELECTED}; }}
QStatusBar {{ background: transparent; color: {MUTED}; }}

/* Sidebar last: NavButton must beat the generic QPushButton rules above. */
/* ── Sidebar ────────────────────────────────────────────── */
QLabel#SidebarTitle {{
    font-family: {FONT_DISPLAY};
    color: {TEXT};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#SidebarSection {{
    color: {SUBTLE};
    font-size: 10px;
    font-weight: 700;
    padding: 10px 14px 4px 14px;
}}
QLabel#SidebarGroup {{
    color: {SUBTLE};
    font-size: 10px;
    font-weight: 700;
    padding: 8px 14px 2px 14px;
}}
QLabel#BookBadge {{
    color: {PRIMARY};
    background: {SELECTED};
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
}}
QComboBox#DbSelector {{
    background: {SELECTED};
    color: {TEXT};
    border: 1px solid rgba(47, 107, 255, 0.10);
    border-radius: 12px;
    padding: 8px 28px 8px 12px;
    font-size: 12px;
    font-weight: 600;
    min-width: 0;
}}
QComboBox#DbSelector:hover {{ border-color: {BLUE_EDGE_HI}; }}
NavButton {{
    background: transparent;
    color: {MUTED};
    text-align: left;
    padding: 9px 14px;
    border: none;
    border-radius: 12px;
    font-size: 13px;
    font-weight: 500;
}}
NavButton:hover {{
    background: {HOVER};
    color: {TEXT};
}}
NavButton[active="true"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(47, 107, 255, 0.16), stop:1 rgba(91, 155, 255, 0.08));
    color: {PRIMARY};
    font-weight: 600;
}}
QPushButton#SidebarLink {{
    background: transparent;
    color: {MUTED};
    border: none;
    text-align: left;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 500;
    border-radius: 10px;
}}
QPushButton#SidebarLink:hover {{ background: {HOVER}; color: {PRIMARY}; }}
QLabel#SidebarStatus {{
    color: {SUBTLE};
    font-size: 11px;
    padding: 2px 14px 4px 14px;
}}

"""


QSS = build_qss()


def apply_theme(app) -> None:
    """Install fonts, palette and stylesheet on the QApplication."""
    from PyQt6.QtGui import QFont, QFontDatabase, QPalette, QColor

    families = set(QFontDatabase.families())
    for fam in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
        if fam in families:
            font = QFont(fam, 10)
            break
    else:
        font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    pal = app.palette()
    for role, color in (
        (QPalette.ColorRole.Window, BG_MID),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#f6f9ff"),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Button, "#ffffff"),
        (QPalette.ColorRole.Highlight, PRIMARY),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, "#ffffff"),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, SUBTLE),
        (QPalette.ColorRole.Link, PRIMARY),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)

    try:
        icons = _render_theme_icons()
    except Exception:
        icons = {}
    app.setStyleSheet(build_qss(icons))

    try:
        global _title_tint
        _title_tint = _TitleBarTint(app)
        app.installEventFilter(_title_tint)
        # A Python event filter must not outlive the event loop: during
        # interpreter teardown it can be called after Python objects are gone.
        app.aboutToQuit.connect(lambda: app.removeEventFilter(_title_tint))
    except Exception:
        pass


def _colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def tint_title_bar(widget) -> None:
    """Windows 11: give a window's native title bar the theme's light blue and
    rounded corners. Silently does nothing on other systems / older Windows."""
    try:
        import sys
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        set_attr(20, 0)                          # DWMWA_USE_IMMERSIVE_DARK_MODE off
        set_attr(33, 2)                          # DWMWA_WINDOW_CORNER_PREFERENCE round
        set_attr(34, _colorref("#d6e2fb"))       # DWMWA_BORDER_COLOR
        set_attr(35, _colorref(BG_TOP))          # DWMWA_CAPTION_COLOR
        set_attr(36, _colorref(TEXT))            # DWMWA_TEXT_COLOR
    except Exception:
        pass


_title_tint = None


def _make_title_tint_class():
    from PyQt6.QtCore import QObject, QEvent, Qt

    class _Tint(QObject):
        """Tints the title bar of every top-level window the first time it shows."""
        def eventFilter(self, obj, event):
            try:
                if (event.type() == QEvent.Type.Show and obj.isWidgetType()
                        and obj.isWindow()
                        and obj.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)
                        and not obj.property("_fb_tinted")):
                    obj.setProperty("_fb_tinted", True)
                    tint_title_bar(obj)
            except Exception:
                pass
            return False

    return _Tint


def _TitleBarTint(parent):
    return _make_title_tint_class()(parent)
