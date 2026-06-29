SIDEBAR_BG   = "#1e3a5f"
SIDEBAR_SEL  = "#2d5986"
ACCENT       = "#2979ff"
BG           = "#f5f7fa"
CARD_BG      = "#ffffff"
TEXT         = "#1a1a2e"
MUTED        = "#6b7280"
SUCCESS      = "#22c55e"
DANGER       = "#ef4444"
WARNING      = "#f59e0b"
BORDER       = "#e5e7eb"

QSS = f"""
/* ── Global ──────────────────────────────────────────── */
QWidget {{
    font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 13px;
    color: {TEXT};
    background-color: {BG};
}}

/* ── Sidebar ─────────────────────────────────────────── */
#Sidebar {{
    background-color: {SIDEBAR_BG};
    min-width: 210px;
    max-width: 210px;
}}
#SidebarTitle {{
    color: #ffffff;
    font-size: 17px;
    font-weight: bold;
    padding: 22px 18px 8px 18px;
    background: {SIDEBAR_BG};
}}
#SidebarSubtitle {{
    color: #90aac9;
    font-size: 11px;
    padding: 0 18px 20px 18px;
    background: {SIDEBAR_BG};
}}
QComboBox#DbSelector {{
    background: {SIDEBAR_SEL};
    color: #ffffff;
    border: none;
    border-bottom: 1px solid #1a3050;
    border-radius: 0;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: bold;
    min-width: 0;
}}
QComboBox#DbSelector::drop-down {{
    border: none;
    width: 20px;
    padding-right: 8px;
}}
QComboBox#DbSelector QAbstractItemView {{
    background: #1e3a5f;
    color: #ffffff;
    border: 1px solid #1a3050;
    selection-background-color: {SIDEBAR_SEL};
    outline: none;
}}
NavButton {{
    background: transparent;
    color: #b0c4de;
    text-align: left;
    padding: 11px 18px;
    border: none;
    border-radius: 0;
    font-size: 13px;
}}
NavButton:hover {{
    background: {SIDEBAR_SEL};
    color: #ffffff;
}}
NavButton[active="true"] {{
    background: {SIDEBAR_SEL};
    color: #ffffff;
    border-left: 3px solid {ACCENT};
    padding-left: 15px;
}}
#SidebarStatus {{
    color: #7a9abf;
    font-size: 11px;
    padding: 10px 18px;
    background: {SIDEBAR_BG};
}}

/* ── Cards / Frames ──────────────────────────────────── */
QFrame#Card {{
    background: {CARD_BG};
    border-radius: 8px;
    border: 1px solid {BORDER};
}}

/* ── Page titles ─────────────────────────────────────── */
QLabel#PageTitle {{
    font-size: 20px;
    font-weight: bold;
    color: {TEXT};
}}

/* ── Tables ──────────────────────────────────────────── */
QTableWidget {{
    background: {CARD_BG};
    alternate-background-color: #f9fafb;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: #dbeafe;
    selection-color: {TEXT};
}}
QTableWidget::item {{
    padding: 3px 10px;
    border: none;
}}
QHeaderView::section {{
    background: #f1f5f9;
    color: {MUTED};
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    padding: 7px 10px;
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
}}

/* ── Buttons ─────────────────────────────────────────── */
QPushButton {{
    background: {ACCENT};
    color: white;
    border: none;
    border-radius: 5px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover  {{ background: #1565c0; }}
QPushButton:pressed {{ background: #0d47a1; }}
QPushButton:disabled {{ background: #9ca3af; }}

QPushButton#Secondary {{
    background: white;
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QPushButton#Secondary:hover {{ background: #f1f5f9; }}

QPushButton#Danger {{
    background: {DANGER};
}}
QPushButton#Danger:hover {{ background: #dc2626; }}

QPushButton#Success {{
    background: {SUCCESS};
}}
QPushButton#Success:hover {{ background: #16a34a; }}

/* ── Inputs ──────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: #dbeafe;
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1.5px solid {ACCENT};
}}

QComboBox {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 10px;
    min-width: 140px;
    min-height: 20px;
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: white;
    border: 1px solid {BORDER};
    selection-background-color: #dbeafe;
    outline: none;
}}

QDateEdit {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 10px;
    min-height: 20px;
}}
QDateEdit::drop-down {{ border: none; width: 24px; }}

/* ── Dialogs ─────────────────────────────────────────── */
QDialog {{
    background: {CARD_BG};
}}

/* ── Scrollbars ──────────────────────────────────────── */
QScrollBar:vertical {{
    background: {BG};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #cbd5e1;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Misc ────────────────────────────────────────────── */
QLabel#Muted  {{ color: {MUTED}; font-size: 12px; }}
QLabel#Danger {{ color: {DANGER}; }}
QLabel#Success {{ color: {SUCCESS}; }}
QLabel#Warning {{ color: {WARNING}; }}
QGroupBox {{
    font-weight: bold;
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {MUTED};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: white;
}}
QTabBar::tab {{
    background: #f1f5f9;
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 7px 16px;
    border-radius: 5px 5px 0 0;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: white;
    color: {ACCENT};
    font-weight: bold;
}}
QSplitter::handle {{ background: {BORDER}; }}
"""
