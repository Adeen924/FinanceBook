from datetime import date, timedelta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QFrame, QScrollArea, QGridLayout, QPushButton,
                              QSizePolicy, QButtonGroup)
from PyQt6.QtCore import Qt
from ui.widgets import (PageTitle, MutedLabel, DataTable, GlassCard, IconBubble,
                        FitLabel, DeltaChip, LinkButton, SmoothLineChart,
                        DonutChart, fmt_date)
from ui import styles as S
from ui.styles import SUCCESS, DANGER, WARNING, ACCENT, money

LIABILITY_TYPES = ("credit", "credit card", "loan", "liability")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SLICE_COLORS = ["#2f6bff", "#5b9bff", "#8b7bff", "#22b8f0", "#9fbcff"]
OTHER_COLOR = "#cbd5e1"
RECENT_ROWS = 8


def _month_start(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _pnl_bucket(t: dict, cats: dict) -> str:
    """Same income / expense / debt split the P&L report uses."""
    cat = cats.get(t.get("category_id", ""), {})
    amt = float(t.get("amount") or 0)
    ctype = cat.get("type", "expense")
    if ctype == "income" or (not cat.get("id") and amt > 0):
        return "income"
    if ctype == "debt_repayment":
        return "debt"
    return "expense"


def _section_header(title: str, subtitle: str = "", right: QWidget = None) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(1)
    t = QLabel(title)
    t.setObjectName("CardTitle")
    col.addWidget(t)
    if subtitle:
        s = MutedLabel(subtitle)
        col.addWidget(s)
    row.addLayout(col)
    row.addStretch()
    if right is not None:
        row.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)
    return row


class KpiCard(GlassCard):
    def __init__(self, label: str, icon: str, accent: bool = False, parent=None):
        super().__init__(parent, accent=accent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fg = "#ffffff" if accent else S.TEXT
        muted = "rgba(255,255,255,0.82)" if accent else S.MUTED

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        top = QHBoxLayout()
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(f"color:{muted}; font-size:11px; font-weight:700;")
        top.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        top.addWidget(IconBubble(icon, S.PRIMARY, 34, on_accent=accent))
        lay.addLayout(top)

        self.value = FitLabel("$0.00", max_px=30, min_px=16,
                              css=f"color:{fg}; font-weight:700; font-family:{S.FONT_DISPLAY};")
        lay.addWidget(self.value)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.chip = DeltaChip(on_accent=accent)
        bottom.addWidget(self.chip)
        self.caption = QLabel("")
        self.caption.setStyleSheet(f"color:{muted}; font-size:11px;")
        bottom.addWidget(self.caption)
        bottom.addStretch()
        lay.addLayout(bottom)

    def set_values(self, value: str, chip: str, good, caption: str):
        self.value.setText(value)
        self.chip.set_delta(chip, good)
        self.caption.setText(caption)


class AccountRow(QFrame):
    """One clickable line in the dashboard's Accounts panel."""
    def __init__(self, acct: dict, balance: float, on_click=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AccountRow")
        self._acct_id = acct["id"]
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to view this account's transactions")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 10, 7)
        lay.setSpacing(10)

        liability = acct.get("type", "") in LIABILITY_TYPES
        tint = S.LAVENDER if liability else S.PRIMARY
        initial = QLabel((acct.get("name") or "?")[:1].upper())
        initial.setFixedSize(34, 34)
        initial.setAlignment(Qt.AlignmentFlag.AlignCenter)
        initial.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {tint}, stop:1 {S.PRIMARY_2});"
            "color:white; border-radius:11px; font-weight:700; font-size:14px;")
        lay.addWidget(initial)

        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(acct.get("name", ""))
        name.setStyleSheet("font-weight:600;")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(name)
        sub_bits = [acct.get("type", "").title(), acct.get("institution", "")]
        sub = MutedLabel(" · ".join(b for b in sub_bits if b))
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(sub)
        lay.addLayout(col, 1)

        bal = QLabel(money(balance))
        bal.setStyleSheet(f"font-weight:700; color:{SUCCESS if balance >= 0 else DANGER};")
        lay.addWidget(bal)

    def mousePressEvent(self, event):
        if self._on_click is not None:
            self._on_click(self._acct_id)
        super().mousePressEvent(event)


class DashboardPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._range_months = 6
        self._build()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("ScrollContent")
        scroll.setWidget(content)
        grid = QGridLayout(content)
        # Cards reserve ~10px of shadow on each side, so 0 spacing still gives a
        # ~20px visual gutter between card bodies.
        grid.setContentsMargins(10, 14, 12, 10)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        for c in range(12):
            grid.setColumnStretch(c, 1)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(10, 4, 10, 12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(PageTitle("Dashboard"))
        self._subtitle = QLabel("")
        self._subtitle.setObjectName("PageSubtitle")
        title_col.addWidget(self._subtitle)
        hdr.addLayout(title_col)
        hdr.addStretch()
        self._alert_label = QLabel("")
        self._alert_label.setObjectName("WarnChip")
        self._alert_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._alert_label.setToolTip("Open Transactions to categorize them")
        self._alert_label.mousePressEvent = lambda e: self._goto("Transactions")
        self._alert_label.hide()
        hdr.addWidget(self._alert_label, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addLayout(hdr, 0, 0, 1, 12)

        # KPI row
        self._kpi_net   = KpiCard("Net worth", "wallet", accent=True)
        self._kpi_asset = KpiCard("Assets", "trend_up")
        self._kpi_liab  = KpiCard("Liabilities", "card")
        self._kpi_flow  = KpiCard("Cash flow this month", "flow")
        for i, card in enumerate((self._kpi_net, self._kpi_asset,
                                  self._kpi_liab, self._kpi_flow)):
            grid.addWidget(card, 1, i * 3, 1, 3)

        grid.addWidget(self._build_chart_card(), 2, 0, 1, 8)
        grid.addWidget(self._build_donut_card(), 2, 8, 1, 4)
        grid.addWidget(self._build_recent_card(), 3, 0, 1, 8)
        grid.addWidget(self._build_accounts_card(), 3, 8, 1, 4)
        grid.setRowStretch(4, 1)

    def _build_chart_card(self) -> GlassCard:
        card = GlassCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)

        seg = QFrame()
        seg.setObjectName("SegmentGroup")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        self._range_group = QButtonGroup(self)
        for months, text in ((6, "6M"), (12, "12M")):
            b = QPushButton(text)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setChecked(months == self._range_months)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=months: self._set_range(m))
            self._range_group.addButton(b)
            seg_lay.addWidget(b)
        lay.addLayout(_section_header("Income vs. expenses",
                                      "Monthly totals · transfers excluded", seg))

        legend = QHBoxLayout()
        legend.setSpacing(16)
        for name, color in (("Income", S.PRIMARY), ("Expenses", S.LAVENDER)):
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            legend.addWidget(dot)
            lbl = MutedLabel(name)
            legend.addWidget(lbl)
        legend.addStretch()
        self._chart_totals = QLabel("")
        self._chart_totals.setStyleSheet(f"color:{S.MUTED}; font-size:12px;")
        legend.addWidget(self._chart_totals)
        lay.addLayout(legend)

        self._chart = SmoothLineChart()
        self._chart.setMinimumHeight(240)
        lay.addWidget(self._chart, 1)
        return card

    def _build_donut_card(self) -> GlassCard:
        card = GlassCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(8)
        lay.addLayout(_section_header("Spending by category", "Last 30 days"))
        self._donut = DonutChart()
        self._donut.setMinimumHeight(170)
        lay.addWidget(self._donut, 1)
        self._legend_box = QVBoxLayout()
        self._legend_box.setSpacing(6)
        lay.addLayout(self._legend_box)
        return card

    def _build_recent_card(self) -> GlassCard:
        card = GlassCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(10)
        view_all = LinkButton("View all  →")
        view_all.clicked.connect(lambda: self._goto("Transactions"))
        lay.addLayout(_section_header("Recent transactions",
                                      "Last 30 days · transfers excluded", view_all))

        self._table = DataTable(["Date", "Payee / Memo", "Category", "Amount"], flat=True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, hh.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, hh.ResizeMode.Stretch)
        hh.setStretchLastSection(False)
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(3, 120)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setFixedHeight(38 + 40 * RECENT_ROWS + 4)
        lay.addWidget(self._table)
        self._recent_empty = MutedLabel("No transactions in the last 30 days.")
        self._recent_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._recent_empty.hide()
        lay.addWidget(self._recent_empty)
        lay.addStretch()
        return card

    def _build_accounts_card(self) -> GlassCard:
        card = GlassCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 18, 16, 14)
        lay.setSpacing(8)
        manage = LinkButton("Manage  →")
        manage.clicked.connect(lambda: self._goto("Accounts"))
        hdr = _section_header("Accounts", "Click one to see its register", manage)
        hdr.setContentsMargins(4, 0, 0, 0)
        lay.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._acct_list = QVBoxLayout(holder)
        self._acct_list.setContentsMargins(0, 0, 4, 0)
        self._acct_list.setSpacing(2)
        self._acct_list.addStretch()
        scroll.setWidget(holder)
        scroll.setMinimumHeight(38 + 40 * RECENT_ROWS - 40)
        lay.addWidget(scroll, 1)
        return card

    # ── data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        today = date.today()
        self._subtitle.setText("Here's where your money stands · "
                               f"{today.strftime('%A')}, {MONTHS[today.month - 1]} {today.day}")
        self._cats = {c["id"]: c for c in self.db.get_categories()}
        self._accounts = self.db.get_accounts()
        self._load_kpis()
        self._load_chart()
        self._load_donut()
        self._load_recent()
        self._load_accounts()
        self._load_alerts()

    def _goto(self, page_name: str):
        mw = getattr(self, "_main_window", None)
        if mw is None:
            return
        idx = mw._page_index(page_name)
        if idx is not None:
            mw._nav_to(idx)

    def _open_account(self, account_id: str):
        mw = getattr(self, "_main_window", None)
        if mw is not None:
            mw.open_account_transactions(account_id)

    def _set_range(self, months: int):
        self._range_months = months
        self._load_chart()

    @staticmethod
    def _pct_chip(delta: float, current: float, invert: bool = False):
        prev = current - delta
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "–")
        if abs(delta) < 0.005:
            return "– 0%", None
        if abs(prev) >= 0.01:
            text = f"{arrow} {abs(delta) / abs(prev) * 100:.1f}%"
        else:
            text = f"{arrow} {money(abs(delta), cents=False)}"
        good = delta < 0 if invert else delta > 0
        return text, good

    def _load_kpis(self):
        today = date.today()
        since = (today - timedelta(days=30)).isoformat()
        recent = self.db.get_transactions(start=since)
        moved: dict[str, float] = {}
        for t in recent:
            moved[t.get("account_id", "")] = moved.get(t.get("account_id", ""), 0.0) \
                + float(t.get("amount") or 0)

        self._balances = {}
        assets = liab = assets_d = liab_d = 0.0
        for a in self._accounts:
            bal = self.db.account_balance(a["id"])
            self._balances[a["id"]] = bal
            if a.get("type") in LIABILITY_TYPES:
                liab += bal
                liab_d += moved.get(a["id"], 0.0)
            else:
                assets += bal
                assets_d += moved.get(a["id"], 0.0)
        net, net_d = assets + liab, assets_d + liab_d
        owed, owed_d = -liab, -liab_d

        chip, good = self._pct_chip(net_d, net)
        self._kpi_net.set_values(money(net), chip, good, "vs. 30 days ago")
        chip, good = self._pct_chip(assets_d, assets)
        self._kpi_asset.set_values(money(assets), chip, good, "vs. 30 days ago")
        chip, good = self._pct_chip(owed_d, owed, invert=True)
        self._kpi_liab.set_values(money(owed), chip, good, "vs. 30 days ago")

        # Cash flow month-to-date, compared with the same days of last month.
        start = _month_start(today)
        prev_start = _month_start(today, 1)
        prev_end = min(today.replace(day=1) - timedelta(days=1),
                       prev_start + timedelta(days=today.day - 1))
        flow = prev_flow = 0.0
        for t in self.db.get_transactions(start=prev_start.isoformat(),
                                          include_transfers=False):
            if _pnl_bucket(t, self._cats) == "debt":
                continue
            d, amt = t.get("date", ""), float(t.get("amount") or 0)
            if d >= start.isoformat():
                flow += amt
            elif d <= prev_end.isoformat():
                prev_flow += amt
        diff = flow - prev_flow
        if abs(diff) < 0.005:
            chip, good = "– no change", None
        else:
            chip = f"{'▲' if diff > 0 else '▼'} {money(abs(diff), cents=False)}"
            good = diff > 0
        self._kpi_flow.set_values(money(flow), chip, good, "vs. last month")

    def _load_chart(self):
        today = date.today()
        n = self._range_months
        first = _month_start(today, n - 1)
        keys = []
        for back in range(n - 1, -1, -1):
            m = _month_start(today, back)
            keys.append((m.year, m.month))
        income = {k: 0.0 for k in keys}
        expense = {k: 0.0 for k in keys}
        for t in self.db.get_transactions(start=first.isoformat(), include_transfers=False):
            d = t.get("date", "")
            try:
                k = (int(d[:4]), int(d[5:7]))
            except (ValueError, TypeError):
                continue
            if k not in income:
                continue
            bucket = _pnl_bucket(t, self._cats)
            amt = float(t.get("amount") or 0)
            if bucket == "income":
                income[k] += amt
            elif bucket == "expense":
                expense[k] -= amt
        spans_years = keys[0][0] != keys[-1][0]
        labels = [MONTHS[m - 1] + (f" '{str(y)[2:]}" if spans_years and m == 1 else "")
                  for y, m in keys]
        inc = [max(income[k], 0.0) for k in keys]
        exp = [max(expense[k], 0.0) for k in keys]
        self._chart.set_data(labels, [
            {"name": "Income", "color": S.PRIMARY, "values": inc},
            {"name": "Expenses", "color": S.LAVENDER, "values": exp},
        ], empty_text="No income or expenses recorded in this period yet")
        net = sum(inc) - sum(exp)
        self._chart_totals.setText(
            f"In <b style='color:{S.TEXT}'>{money(sum(inc), False)}</b> &nbsp;·&nbsp; "
            f"Out <b style='color:{S.TEXT}'>{money(sum(exp), False)}</b> &nbsp;·&nbsp; "
            f"Net <b style='color:{SUCCESS if net >= 0 else DANGER}'>{money(net, False)}</b>")

    def _load_donut(self):
        since = (date.today() - timedelta(days=30)).isoformat()
        groups: dict[str, float] = {}
        for t in self.db.get_transactions(start=since, include_transfers=False):
            if _pnl_bucket(t, self._cats) != "expense":
                continue
            cat = self._cats.get(t.get("category_id", ""), {})
            parent = self._cats.get(cat.get("parent_id", ""), {})
            name = parent.get("name") or cat.get("name") or "Uncategorized"
            groups[name] = groups.get(name, 0.0) - float(t.get("amount") or 0)
        items = sorted(((k, v) for k, v in groups.items() if v > 0),
                       key=lambda kv: kv[1], reverse=True)
        top = items[:len(SLICE_COLORS)]
        rest = sum(v for _, v in items[len(SLICE_COLORS):])
        segments = [(k, v, SLICE_COLORS[i]) for i, (k, v) in enumerate(top)]
        if rest > 0:
            segments.append(("Other", rest, OTHER_COLOR))
        total = sum(v for _, v, _ in segments)
        self._donut.set_data(segments, money(total, cents=False) if total else "$0",
                             "spent" if total else "No spending yet")

        while self._legend_box.count():
            item = self._legend_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        for name, value, color in segments:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            rl.addWidget(dot)
            lbl = QLabel(name)
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setStyleSheet(f"color:{S.TEXT_SOFT};")
            rl.addWidget(lbl, 1)
            pct = MutedLabel(f"{value / total * 100:.0f}%")
            rl.addWidget(pct)
            amt = QLabel(money(value, cents=False))
            amt.setStyleSheet("font-weight:600;")
            amt.setMinimumWidth(64)
            amt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(amt)
            self._legend_box.addWidget(row)

    def _load_recent(self):
        start = (date.today() - timedelta(days=30)).isoformat()
        txns = self.db.get_transactions(start=start, include_transfers=False)[:RECENT_ROWS]
        accts = {a["id"]: a for a in self._accounts}

        self._table.clear_rows()
        self._table.setRowCount(len(txns))
        self._table.setVisible(bool(txns))
        self._recent_empty.setVisible(not txns)
        for row, t in enumerate(txns):
            cat = self._cats.get(t.get("category_id", ""), {})
            parent = self._cats.get(cat.get("parent_id", ""), {})
            cat_name = f"{parent['name']} → {cat['name']}" if parent else cat.get("name", "")
            acct_name = accts.get(t.get("account_id", ""), {}).get("name", "—")
            payee = t.get("payee") or t.get("memo") or "—"

            self._table.set_item(row, 0, fmt_date(t.get("date", "")), color=S.MUTED)
            item = self._table.set_item(row, 1, payee, bold=True)
            item.setToolTip(f"{payee}\n{acct_name}")
            if cat_name:
                self._table.set_item(row, 2, cat_name, color=S.TEXT_SOFT)
            else:
                self._table.set_item(row, 2, "⚠ Uncategorized", color="#b45309")
            self._table.money_item(row, 3, float(t.get("amount") or 0), show_sign=True)

    def _load_accounts(self):
        while self._acct_list.count() > 1:
            item = self._acct_list.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self._accounts:
            empty = MutedLabel("No accounts yet — add one from the Accounts page.")
            empty.setWordWrap(True)
            empty.setContentsMargins(6, 8, 6, 8)
            self._acct_list.insertWidget(0, empty)
            return
        ordered = sorted(self._accounts,
                         key=lambda a: (a.get("type", "") in LIABILITY_TYPES,
                                        -abs(self._balances.get(a["id"], 0.0))))
        for i, acct in enumerate(ordered):
            row = AccountRow(acct, self._balances.get(acct["id"], 0.0),
                             on_click=self._open_account)
            self._acct_list.insertWidget(i, row)

    def _load_alerts(self):
        all_txns = self.db.get_transactions(include_transfers=False)
        uncategorized = sum(1 for t in all_txns if not t.get("category_id"))
        if uncategorized:
            noun = "transaction" if uncategorized == 1 else "transactions"
            self._alert_label.setText(f"⚠  {uncategorized} uncategorized {noun}")
        self._alert_label.setVisible(bool(uncategorized))
