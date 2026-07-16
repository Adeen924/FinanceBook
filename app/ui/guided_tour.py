"""
Interactive guided tour ("coach marks").

Unlike a slideshow, this drives the real app: it navigates to each page, dims
everything except one real button/area, and shows a callout pointing at it with
Back / Next / Skip. Used for the first-run walkthrough and from
Settings → Show Welcome Guide.

Each step: nav (page name to switch to, or None to stay), target (callable
receiving (main_window, current_page) → the widget to highlight, or None to
center the callout), title, body.
"""
from PyQt6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout,
                             QHBoxLayout, QFrame)
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QTimer, QEvent
from PyQt6.QtGui import QPainter, QPainterPath, QColor, QPen


STEPS = [
    dict(nav="Dashboard", target=None,
         title="Welcome — let's take a quick tour",
         body="I'll walk you to each page and point out the exact button to "
              "click. Use <b>Next</b> and <b>Back</b>, or <b>Skip</b> anytime."),

    dict(nav="Dashboard", target=lambda mw, p: getattr(mw, "_sidebar", None),
         title="The sidebar",
         body="Every page lives here. Click any item to jump to it. "
              "<b>Settings</b> (at the bottom) holds the import wizard and this guide."),

    dict(nav="Accounts", target=lambda mw, p: getattr(p, "_add_btn", None),
         title="Step 1 — Add your accounts",
         body="Click <b>“+ Add Account”</b> to create each bank, credit-card, "
              "loan or cash account and give it a name and starting balance."),

    dict(nav="Accounts",
         target=lambda mw, p: (p.first_account_name_rect()
                               if hasattr(p, "first_account_name_rect") else None),
         title="Open an account's transactions",
         body="Once you have accounts, click an account's <b>blue name</b> here "
              "(or its card on the Dashboard) to jump straight to that account's "
              "transactions."),

    dict(nav="Import", target=lambda mw, p: getattr(p, "_acct_combo", None),
         title="Step 2 — Import from your bank",
         body="1) Choose the account here. &nbsp; 2) <b>Browse</b> for your "
              "OFX / QFX / CSV / Excel file. &nbsp; 3) Click <b>Parse File</b>, "
              "review, then <b>Import All</b>."),

    dict(nav="Settings", target=lambda mw, p: getattr(p, "_wizard_btn", None),
         title="Moving from QuickBooks?",
         body="Click <b>“Open Import Wizard”</b>. <b>Step 1</b> brings in your "
              "accounts &amp; categories (IIF file); <b>Step 2</b> brings in your "
              "transactions (Excel “Transaction Detail by Account”)."),

    dict(nav="Transactions", target=lambda mw, p: getattr(p, "_add_btn", None),
         title="Step 3 — Add & manage transactions",
         body="Click <b>“+ Add Transaction”</b>. From that one screen you can also "
              "<b>Split</b> a purchase across categories or mark it a "
              "<b>Transfer</b> between accounts."),

    dict(nav="Dashboard", target=None,
         title="You're all set!",
         body="You can reopen this tour anytime from "
              "<b>Settings → Show Welcome Guide</b>. Enjoy FinanceBook!"),
]


class GuidedTour:
    def __init__(self, main_window):
        self.mw = main_window
        self.i = 0
        self.overlay: _Overlay | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def start(self):
        if not STEPS:
            return
        self.overlay = _Overlay(self.mw, self)
        self.overlay.show()
        self.overlay.raise_()
        self.i = 0
        self._show()

    def finish(self):
        if self.overlay is not None:
            self.overlay.cleanup()
            self.overlay.deleteLater()
            self.overlay = None

    # ── navigation ──────────────────────────────────────────────────────────────

    def next(self):
        if self.i >= len(STEPS) - 1:
            self.finish()
            return
        self.i += 1
        self._show()

    def back(self):
        if self.i > 0:
            self.i -= 1
            self._show()

    def _show(self):
        step = STEPS[self.i]
        # Navigate to the page for this step.
        nav = step.get("nav")
        if nav:
            try:
                idx = self.mw._page_index(nav)
                if idx is not None:
                    self.mw._nav_to(idx)
            except Exception:
                pass
        # Resolve the target widget after the page has laid out.
        self.overlay.raise_()
        QTimer.singleShot(30, self._apply_step)

    def _apply_step(self):
        step = STEPS[self.i]
        page = self.mw._stack.currentWidget()
        target = None
        getter = step.get("target")
        if getter is not None:
            try:
                target = getter(self.mw, page)
            except Exception:
                target = None
            if (target is not None and not isinstance(target, QRect)
                    and not target.isVisible()):
                target = None
        self.overlay.set_step(
            target=target,
            title=step["title"],
            body=step["body"],
            index=self.i,
            total=len(STEPS),
        )


class _Overlay(QWidget):
    """Full-window dim layer with a highlight cut-out and a callout bubble."""
    def __init__(self, main_window, tour: GuidedTour):
        super().__init__(main_window)
        self.mw = main_window
        self.tour = tour
        self._target_rect: QRect | None = None
        self.setGeometry(main_window.rect())
        self.mw.installEventFilter(self)

        # Callout bubble
        self._callout = QFrame(self)
        self._callout.setObjectName("TourCallout")
        self._callout.setStyleSheet(
            "QFrame#TourCallout { background:#ffffff; border:1px solid #cbd5e1; "
            "border-radius:10px; }")
        self._callout.setFixedWidth(370)
        cl = QVBoxLayout(self._callout)
        cl.setContentsMargins(18, 16, 18, 14)
        cl.setSpacing(8)

        self._step_lbl = QLabel("")
        self._step_lbl.setStyleSheet("color:#2563eb; font-size:11px; font-weight:bold;")
        cl.addWidget(self._step_lbl)

        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet("font-size:16px; font-weight:bold; color:#0f172a;")
        self._title_lbl.setWordWrap(True)
        cl.addWidget(self._title_lbl)

        self._body_lbl = QLabel("")
        self._body_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setStyleSheet("font-size:13px; color:#334155;")
        cl.addWidget(self._body_lbl)

        btns = QHBoxLayout()
        self._skip = QPushButton("Skip")
        self._skip.setStyleSheet(self._btn_css(secondary=True))
        self._skip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip.clicked.connect(self.tour.finish)
        btns.addWidget(self._skip)
        btns.addStretch()
        self._back = QPushButton("Back")
        self._back.setStyleSheet(self._btn_css(secondary=True))
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(self.tour.back)
        btns.addWidget(self._back)
        self._next = QPushButton("Next")
        self._next.setStyleSheet(self._btn_css(secondary=False))
        self._next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next.clicked.connect(self.tour.next)
        btns.addWidget(self._next)
        cl.addLayout(btns)

    @staticmethod
    def _btn_css(secondary: bool) -> str:
        if secondary:
            return ("QPushButton { padding:6px 14px; border-radius:6px; "
                    "background:#f1f5f9; color:#0f172a; border:1px solid #e2e8f0; }"
                    "QPushButton:hover { background:#e2e8f0; }")
        return ("QPushButton { padding:6px 16px; border-radius:6px; "
                "background:#2563eb; color:white; border:none; font-weight:bold; }"
                "QPushButton:hover { background:#1d4ed8; }")

    # ── step content ────────────────────────────────────────────────────────────

    def set_step(self, target, title, body, index, total):
        self._step_lbl.setText(f"STEP {index + 1} OF {total}")
        self._title_lbl.setText(title)
        self._body_lbl.setText(body)
        self._back.setEnabled(index > 0)
        is_last = index == total - 1
        self._next.setText("Finish" if is_last else "Next")
        self._skip.setVisible(not is_last)

        if target is None:
            self._target_rect = None
        elif isinstance(target, QRect):
            tl = self.mapFromGlobal(target.topLeft())
            self._target_rect = QRect(tl, target.size()).adjusted(-6, -6, 6, 6)
        else:
            tl = self.mapFromGlobal(target.mapToGlobal(QPoint(0, 0)))
            self._target_rect = QRect(tl, target.size()).adjusted(-6, -6, 6, 6)

        self._layout_callout()
        self.update()

    def _layout_callout(self):
        self._callout.adjustSize()
        cw, ch = self._callout.width(), self._callout.height()
        W, H = self.width(), self.height()
        m = 16
        r = self._target_rect

        if r is None:
            self._callout.move((W - cw) // 2, (H - ch) // 2)
            return

        # Horizontal position for below/above (aligned to target, clamped);
        # vertical position for left/right (centered on target, clamped).
        hx = min(max(m, r.left()), W - cw - m)
        vy = min(max(m, r.center().y() - ch // 2), H - ch - m)
        candidates = [
            QRect(hx, r.bottom() + m, cw, ch),        # below
            QRect(r.right() + m, vy, cw, ch),         # right
            QRect(r.left() - m - cw, vy, cw, ch),     # left
            QRect(hx, r.top() - m - ch, cw, ch),      # above
        ]

        def on_screen(rc):
            return (rc.left() >= 0 and rc.top() >= 0
                    and rc.right() <= W and rc.bottom() <= H)

        # First placement that fits fully and doesn't cover the target.
        for rc in candidates:
            if on_screen(rc) and not rc.intersects(r):
                self._callout.move(rc.topLeft())
                return

        # Fallback: clamp each on-screen, pick the one covering the target least.
        def overlap_area(rc):
            ir = rc.intersected(r)
            return ir.width() * ir.height() if not ir.isEmpty() else 0

        clamped = [QRect(min(max(m, rc.left()), W - cw - m),
                         min(max(m, rc.top()), H - ch - m), cw, ch)
                   for rc in candidates]
        best = min(clamped, key=overlap_area)
        self._callout.move(best.topLeft())

    # ── painting & events ─────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        if self._target_rect is not None:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self._target_rect), 8, 8)
            p.fillPath(full.subtracted(hole), QColor(15, 23, 42, 160))
            pen = QPen(QColor("#3b82f6"))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(self._target_rect, 8, 8)
        else:
            p.fillPath(full, QColor(15, 23, 42, 160))

    def mousePressEvent(self, event):
        # Swallow clicks on the dimmed area so the app can't be used mid-tour;
        # the callout's own buttons still work (they're child widgets).
        event.accept()

    def eventFilter(self, obj, event):
        if obj is self.mw and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.mw.rect())
            QTimer.singleShot(0, self.tour._apply_step)
        return False

    def cleanup(self):
        try:
            self.mw.removeEventFilter(self)
        except Exception:
            pass
