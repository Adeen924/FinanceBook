"""Reports: Profit & Loss (with sub-accounts and period columns), Balance Sheet,
and the extra report tabs in ui/report_tabs.py.

v1.18: the shared filter bar has a "Columns:" dropdown — Day, Week, Month,
Quarter, Year or Total (utils.reports.period_buckets). The P&L shows one
column per period plus Total; the old "By Month" tab is folded into it (the
"By Month" / "By Period" names open the P&L with Month columns). By Class, By
Vendor and Cash Flow use the same choice; the Balance Sheet is point-in-time.
More than MANY_COLUMNS columns asks first, and the account column stays put
while the amounts scroll sideways.
"""
from datetime import date
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QTabWidget, QComboBox,
                              QTreeWidget, QTreeWidgetItem, QHeaderView,
                              QFrame, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QColor, QFont
from ui.widgets import PageTitle, SecondaryButton, DateField, fmt_date, FrozenColumn
from ui.styles  import SUCCESS, DANGER, TEXT, MUTED, WARNING
from utils import reports as RP

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
PNL_TAB, BS_TAB = 0, 1
PNL_LABEL = "Profit & Loss"

# Old report names that still open something: the By Month tab became the
# P&L's Month columns in v1.18.
REPORT_ALIASES = {
    "By Month": (PNL_LABEL, "month"),
    "By Period": (PNL_LABEL, "month"),
    "Profit & Loss": (PNL_LABEL, None),
    "Profit && Loss": (PNL_LABEL, None),
    "P&L": (PNL_LABEL, None),
}

# Default Columns choice per report (anything not listed: Total).
DEFAULT_PERIOD = {PNL_LABEL: "total"}

_COLLAPSED = Qt.ItemDataRole.UserRole + 10      # amounts a heading shows when collapsed


def _money(amt) -> str:
    amt = float(amt)
    return f"${amt:,.2f}" if amt >= 0 else f"(${abs(amt):,.2f})"


class ReportsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._tab_period: dict = {}          # tab index → Columns choice
        self._confirmed = None               # (tab, period, start, end) the user OK'd
        self._pnl_report = None
        self._pnl_columns: list = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        lay.addWidget(PageTitle("Reports"))

        # Date/class filter bar (shared)
        filt = QHBoxLayout()
        filt.setSpacing(8)
        filt.addWidget(QLabel("From:"))
        self._date_from = DateField()
        self._date_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        filt.addWidget(self._date_from)

        filt.addWidget(QLabel("To:"))
        self._date_to = DateField()
        self._date_to.setDate(QDate.currentDate())
        filt.addWidget(self._date_to)

        self._class_combo = QComboBox()
        self._class_combo.setMinimumWidth(160)
        self._class_combo.addItem("All Classes", "")
        filt.addWidget(self._class_combo)

        self._period_lbl = QLabel("Columns:")
        filt.addWidget(self._period_lbl)
        # A plain QComboBox: the app theme gives it the shared dropdown look
        # (list opens below, clear highlight) — see ui/styles.py.
        self._period_combo = QComboBox()
        self._period_combo.setMinimumWidth(110)
        for key, label in RP.period_choices():
            self._period_combo.addItem(label, key)
        self._period_combo.setCurrentIndex(self._period_combo.findData("total"))
        self._period_combo.setToolTip("One column per day, week (Monday–Sunday), month, "
                                      "quarter or year, plus Total")
        self._period_combo.currentIndexChanged.connect(self._on_period_changed)
        filt.addWidget(self._period_combo)

        run_btn = QPushButton("Run Report")
        run_btn.clicked.connect(self._run)
        filt.addWidget(run_btn)

        export_btn = SecondaryButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        filt.addWidget(export_btn)

        filt.addStretch()
        lay.addLayout(filt)

        self._tabs = QTabWidget()

        # ── P&L tab ──
        pnl = QWidget()
        pnl_lay = QVBoxLayout(pnl)
        pnl_lay.setSpacing(8)

        top = QHBoxLayout()
        self._pnl_summary = QLabel("")
        self._pnl_summary.setWordWrap(True)
        self._pnl_summary.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self._pnl_summary, 1)
        self._expand_all_btn = SecondaryButton("Expand all")
        self._expand_all_btn.clicked.connect(self.expand_all)
        top.addWidget(self._expand_all_btn)
        self._collapse_all_btn = SecondaryButton("Collapse all")
        self._collapse_all_btn.clicked.connect(self.collapse_all)
        top.addWidget(self._collapse_all_btn)
        pnl_lay.addLayout(top)

        self._pnl_tree = QTreeWidget()
        self._pnl_tree.setHeaderLabels(["Account", "Total"])
        self._pnl_tree.setColumnWidth(0, 400)
        self._pnl_tree.setAlternatingRowColors(True)
        self._pnl_tree.setUniformRowHeights(True)
        self._pnl_tree.header().setStretchLastSection(True)
        self._pnl_tree.itemExpanded.connect(self._show_amounts)
        self._pnl_tree.itemCollapsed.connect(self._show_amounts)
        pnl_lay.addWidget(self._pnl_tree)
        self._pnl_frozen = FrozenColumn(self._pnl_tree)

        self._pnl_note = QLabel(
            "Expenses and cost of goods sold are shown as positive amounts. Computed "
            "lines (depreciation, asset-sale gains, inventory cost of goods sold) are in "
            "the period they belong to; depreciation is worked out by whole month, so "
            "with Day or Week columns each month's depreciation is in the column that "
            "holds the month's last day.")
        self._pnl_note.setObjectName("Muted")
        self._pnl_note.setWordWrap(True)
        pnl_lay.addWidget(self._pnl_note)
        self._tabs.addTab(pnl, "Profit && Loss")

        # ── Balance Sheet tab ──
        bs = QWidget()
        bs_lay = QVBoxLayout(bs)
        bs_lay.setSpacing(8)

        self._bs_as_of = QHBoxLayout()
        self._bs_as_of_lbl = QLabel("")
        self._bs_as_of_lbl.setObjectName("Muted")
        self._bs_as_of.addWidget(self._bs_as_of_lbl)
        self._bs_as_of.addStretch()
        bs_lay.addLayout(self._bs_as_of)

        self._bs_tree = QTreeWidget()
        self._bs_tree.setHeaderLabels(["Account", "Balance"])
        self._bs_tree.setColumnWidth(0, 400)
        self._bs_tree.setAlternatingRowColors(True)
        self._bs_tree.header().setStretchLastSection(True)
        bs_lay.addWidget(self._bs_tree)
        self._tabs.addTab(bs, "Balance Sheet")

        # More reports (each tab knows how to run and export itself).
        from ui.report_tabs import extra_tabs
        self._tabs.setUsesScrollButtons(True)
        for label, cls in extra_tabs(self.db):
            self._tabs.addTab(cls(self.db), label)

        self._tabs.currentChanged.connect(self._on_tab_changed)
        lay.addWidget(self._tabs)
        self._sync_period_combo()

    # ── tabs and the Columns choice ───────────────────────────────────────────

    def _tab_label(self, i: int) -> str:
        return self._tabs.tabText(i).replace("&&", "&")

    def _supports_period(self, i: int) -> bool:
        if i == PNL_TAB:
            return True
        if i == BS_TAB:
            return False
        return bool(getattr(self._tabs.widget(i), "supports_period", False))

    def period(self, i: int = None) -> str:
        """The Columns choice for a tab (the current one by default)."""
        i = self._tabs.currentIndex() if i is None else i
        if not self._supports_period(i):
            return "total"
        return self._tab_period.get(i, DEFAULT_PERIOD.get(self._tab_label(i), "total"))

    def _set_combo(self, key: str):
        self._period_combo.blockSignals(True)
        self._period_combo.setCurrentIndex(max(self._period_combo.findData(key), 0))
        self._period_combo.blockSignals(False)

    def _sync_period_combo(self):
        i = self._tabs.currentIndex()
        ok = self._supports_period(i)
        self._set_combo(self.period(i) if ok else "total")
        self._period_combo.setEnabled(ok)
        self._period_lbl.setEnabled(ok)
        self._period_combo.setToolTip(
            "One column per day, week (Monday–Sunday), month, quarter or year, plus Total"
            if ok else "This report has no columns over time.")

    def _on_tab_changed(self, _i):
        self._sync_period_combo()
        self._run()

    def _dates(self) -> tuple:
        return (self._date_from.date().toString("yyyy-MM-dd"),
                self._date_to.date().toString("yyyy-MM-dd"))

    def _confirm_columns(self, n: int) -> bool:
        return QMessageBox.question(
            self, "Many Columns",
            f"That's {n} columns. Continue?\n\nThe account names stay on the left while "
            "you scroll across.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    def _column_count(self, period: str) -> int:
        start, end = self._dates()
        if period == "total" or end < start:
            return 1
        return len(RP.period_buckets(start, end, period))

    def _allowed(self, i: int, period: str) -> bool:
        """True if `period` may be used now: few enough columns, or the user
        said yes to this many for these dates."""
        n = self._column_count(period)
        if n <= RP.MANY_COLUMNS:
            return True
        key = (i, period) + self._dates()
        if self._confirmed == key:
            return True
        if self._confirm_columns(n):
            self._confirmed = key
            return True
        return False

    def _on_period_changed(self, _idx):
        i = self._tabs.currentIndex()
        new = self._period_combo.currentData() or "total"
        if not self._allowed(i, new):
            self._set_combo(self.period(i))     # put the previous choice back
            return
        self._tab_period[i] = new
        self._run()

    def show_report(self, name: str, period: str = None):
        """Open a report by its tab name or an old name ("By Month" → the P&L
        with Month columns). Returns True if found."""
        target, alias_period = REPORT_ALIASES.get(name, (name, None))
        period = period or alias_period
        for i in range(self._tabs.count()):
            if self._tab_label(i) == target:
                if period and self._supports_period(i):
                    self._tab_period[i] = period
                if self._tabs.currentIndex() == i:
                    self._sync_period_combo()
                    self._run()
                else:
                    self._tabs.setCurrentIndex(i)
                return True
        return False

    # ── running ───────────────────────────────────────────────────────────────

    def refresh(self):
        classes = self.db.get_classes()
        self._class_combo.blockSignals(True)
        cur = self._class_combo.currentData()
        self._class_combo.clear()
        self._class_combo.addItem("All Classes", "")
        for cls in classes:
            self._class_combo.addItem(cls["name"], cls["id"])
        for i in range(self._class_combo.count()):
            if self._class_combo.itemData(i) == cur:
                self._class_combo.setCurrentIndex(i)
                break
        self._class_combo.blockSignals(False)
        self._run()

    def _run(self):
        idx = self._tabs.currentIndex()
        period = self.period(idx)
        if period != "total" and not self._allowed(idx, period):
            # e.g. Day columns kept while the dates grew to a whole year.
            period = "month" if self._column_count("month") <= RP.MANY_COLUMNS else "total"
            self._tab_period[idx] = period
            self._set_combo(period)
        if idx == PNL_TAB:
            self._run_pnl()
        elif idx == BS_TAB:
            self._run_bs()
        else:
            start, end = self._dates()
            w = self._tabs.currentWidget()
            if getattr(w, "supports_period", False):
                w.run(start, end, self._class_combo.currentData() or "", period)
            else:
                w.run(start, end, self._class_combo.currentData() or "")

    # ── Profit & Loss ─────────────────────────────────────────────────────────

    def pnl_report(self, period: str = None):
        """(report, buckets) for the P&L as the filters stand (see
        utils.reports.pnl_report)."""
        start, end = self._dates()
        period = period or self.period(PNL_TAB)
        cls_filter = self._class_combo.currentData() or ""
        buckets = RP.period_buckets(start, end, period) if end >= start else []
        # Non-cash lines (depreciation, gains, COGS) are company-wide, so
        # they're left out when filtering to a single class.
        rows = self.db.report_rows(start, end, with_computed=not cls_filter, period=period)
        txns = self.db.get_transactions(start=start, end=end, include_transfers=False)
        if cls_filter:
            rows = [r for r in rows if r.get("class_id") == cls_filter]
            txns = [t for t in txns if t.get("class_id") == cls_filter]
        cats = self.db.category_map()
        rep = RP.pnl_report(rows, cats, [b.key for b in buckets if b.key != RP.TOTAL],
                            lambda r: RP.period_key(r["date"], period),
                            memo_rows=RP.memo_rows(txns, cats))
        return rep, buckets

    def _run_pnl(self):
        start, end = self._dates()
        rep, buckets = self.pnl_report()
        self._pnl_report = rep
        cols = rep["columns"]
        self._pnl_columns = cols
        labels = [b.label for b in buckets if b.key != RP.TOTAL] + ["Total"]
        tree = self._pnl_tree
        tree.clear()
        tree.setColumnCount(1 + len(cols))
        tree.setHeaderLabels(["Account"] + labels)
        many = len(cols) > 1
        tree.header().setStretchLastSection(not many)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree.setColumnWidth(0, 340 if many else 400)
        for c in range(1, len(cols) + 1):
            tree.header().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            if many:
                tree.setColumnWidth(c, 104)
        for c in range(1, len(cols) + 1):
            tree.headerItem().setTextAlignment(c, RIGHT)

        def amounts(item, amts, bold=False, color=True, blank_zero=True):
            for c, key in enumerate(cols, start=1):
                v = (amts or {}).get(key)
                if v is None or (v == 0 and blank_zero and key != RP.TOTAL):
                    text = "—" if amts is not None else ""
                else:
                    text = _money(v)
                item.setText(c, text)
                item.setTextAlignment(c, RIGHT)
                if color and v:
                    item.setForeground(c, QColor(SUCCESS if v >= 0 else DANGER))
                elif text == "—":
                    item.setForeground(c, QColor(MUTED))
                if bold:
                    f = item.font(c); f.setBold(True); item.setFont(c, f)
            if bold:
                f = item.font(0); f.setBold(True); item.setFont(0, f)

        def heading(text, collapsed):
            it = QTreeWidgetItem([text])
            it.setData(0, _COLLAPSED, collapsed)
            return it

        def node_item(node):
            if not node["children"]:
                it = QTreeWidgetItem([node["label"]])
                amounts(it, node["total"])
                return it
            it = heading(node["label"], node["total"])
            for ch in node["children"]:
                it.addChild(node_item(ch))
            if node["has_own"]:
                other = QTreeWidgetItem([f"{node['label']} (other)"])
                other.setToolTip(0, f"Entries filed under “{node['label']}” itself, not "
                                    "one of its sub-accounts")
                amounts(other, node["own"])
                it.addChild(other)
            tot = QTreeWidgetItem([f"Total {node['label']}"])
            amounts(tot, node["total"], bold=True, color=False)
            it.addChild(tot)
            return it

        def section_item(sec):
            it = heading(sec["label"], sec["total"])
            for n in sec["nodes"]:
                it.addChild(node_item(n))
            tot = QTreeWidgetItem([f"Total {sec['label']}"])
            amounts(tot, sec["total"], bold=True, color=False, blank_zero=False)
            it.addChild(tot)
            return it

        for b in rep["blocks"]:
            if b["type"] == "section":
                tree.addTopLevelItem(section_item(b))
            elif b["type"] == "summary":
                it = QTreeWidgetItem([b["label"]])
                amounts(it, b["amounts"], bold=True, blank_zero=False)
                if b["label"] == "Net Income":
                    f = it.font(0); f.setPointSize(f.pointSize() + 1)
                    for c in range(tree.columnCount()):
                        it.setFont(c, f)
                tree.addTopLevelItem(it)
            else:
                grp = QTreeWidgetItem([b["label"]])
                for s in b["sections"]:
                    grp.addChild(section_item(s))
                tree.addTopLevelItem(grp)
        tree.expandAll()
        self._refresh_amount_texts()
        self._pnl_frozen.sync(enabled=many)
        self._pnl_summary.setText(self._summary_html(rep, start, end))

    def _summary_html(self, rep, start, end) -> str:
        tot = RP.TOTAL

        def sec_total(label):
            for b in rep["blocks"]:
                for s in ([b] if b["type"] == "section" else b.get("sections", [])):
                    if s["label"] == label:
                        return float(s["total"].get(tot, 0))
            return None

        def summary(label):
            b = next((b for b in rep["blocks"] if b["type"] == "summary"
                      and b["label"] == label), None)
            return float(b["amounts"].get(tot, 0)) if b else None

        income, expenses = sec_total("Income") or 0.0, sec_total("Expenses") or 0.0
        cogs, gross = sec_total(RP.COGS_LABEL), summary("Gross Profit")
        oi, oe = sec_total("Other Income"), sec_total("Other Expenses")
        net = summary("Net Income") or 0.0
        debt = sec_total(RP.MEMO_DEBT)
        parts = [f"<b>Period:</b> {fmt_date(start)} to {fmt_date(end)}",
                 f"<b>Income:</b> <span style='color:{SUCCESS}'>${income:,.2f}</span>"]
        if cogs is not None:
            parts.append(f"<b>Cost of goods sold:</b> <span style='color:{DANGER}'>"
                         f"${cogs:,.2f}</span>")
            parts.append(f"<b>Gross profit:</b> ${gross:,.2f}")
        parts.append(f"<b>Expenses:</b> <span style='color:{DANGER}'>${expenses:,.2f}</span>")
        if oi is not None or oe is not None:
            parts.append(f"<b>Other (net):</b> ${(oi or 0) - (oe or 0):,.2f}")
        color = SUCCESS if net >= 0 else DANGER
        parts.append(f"<b>Net:</b> <span style='color:{color}'>${net:,.2f}</span>")
        if debt:
            parts.append(f"<b>Debt Repayment:</b> <span style='color:{DANGER}'>"
                         f"${abs(debt):,.2f}</span>")
        return " &nbsp;&nbsp; ".join(parts)

    def _items(self):
        tree = self._pnl_tree
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _show_amounts(self, item):
        """A heading shows its total while collapsed; expanded, the total is on
        its "Total …" row instead."""
        amts = item.data(0, _COLLAPSED)
        if amts is None:
            return
        cols = self._pnl_columns
        for c, key in enumerate(cols, start=1):
            if item.isExpanded():
                item.setText(c, "")
                continue
            v = amts.get(key)
            item.setText(c, _money(v) if v else ("$0.00" if key == RP.TOTAL else "—"))
            item.setTextAlignment(c, RIGHT)
            item.setForeground(c, QColor(MUTED if not v else (SUCCESS if v >= 0 else DANGER)))

    def _refresh_amount_texts(self):
        for it in self._items():
            if it.childCount():
                self._show_amounts(it)

    def expand_all(self):
        self._pnl_tree.expandAll()
        self._pnl_frozen.view.expandAll()
        self._refresh_amount_texts()

    def collapse_all(self):
        self._pnl_tree.collapseAll()
        self._pnl_frozen.view.collapseAll()
        self._refresh_amount_texts()

    def _balance_sheet(self, as_of: str) -> dict:
        """Balance sheet data from the shared, tested ledger math."""
        from utils.ledger import balance_sheet
        cats = self.db.category_map()
        fy_start = f"{as_of[:4]}-01-01"
        return balance_sheet(self.db.get_accounts(active=None),
                             self.db.get_transactions(end=as_of), cats, as_of, fy_start,
                             **self.db.balance_sheet_extras(as_of, fy_start))

    def _run_bs(self):
        as_of = self._date_to.date().toString("yyyy-MM-dd")
        self._bs_as_of_lbl.setText(f"As of {fmt_date(as_of)}")
        if self.db.is_business():
            self._run_business_bs(as_of)
            return
        bs = self._balance_sheet(as_of)
        # Personal books keep the familiar layout: liabilities as (negative)
        # balances and Net Worth at the bottom.
        assets = [{"name": n, "balance": float(v)} for n, v, _ in bs["assets"]]
        liabilities = [{"name": n, "balance": -float(v)} for n, v, _ in bs["liabilities"]]

        def _money(amt):
            return f"${amt:,.2f}" if amt >= 0 else f"(${abs(amt):,.2f})"

        self._bs_tree.clear()
        from PyQt6.QtGui import QColor

        def _add_section(label, items):
            root = QTreeWidgetItem([label, ""])
            total = 0.0
            for a in items:
                item = QTreeWidgetItem([a["name"], _money(a["balance"])])
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(1, QColor(SUCCESS if a["balance"] >= 0 else DANGER))
                root.addChild(item)
                total += a["balance"]
            tot_item = QTreeWidgetItem([f"Total {label}", _money(total)])
            tot_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            f = tot_item.font(0); f.setBold(True); tot_item.setFont(0, f); tot_item.setFont(1, f)
            tot_item.setForeground(1, QColor(SUCCESS if total >= 0 else DANGER))
            root.addChild(tot_item)
            root.setExpanded(True)
            return root, total

        assets_root, total_assets = _add_section("Assets", assets)
        liab_root, total_liab    = _add_section("Liabilities", liabilities)
        self._bs_tree.addTopLevelItem(assets_root)
        self._bs_tree.addTopLevelItem(liab_root)

        equity = total_assets + total_liab
        eq_item = QTreeWidgetItem(["Net Worth / Equity", _money(equity)])
        eq_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        eq_item.setForeground(1, QColor(SUCCESS if equity >= 0 else DANGER))
        f = eq_item.font(0); f.setBold(True); f.setPointSize(f.pointSize()+2)
        eq_item.setFont(0, f); eq_item.setFont(1, f)
        self._bs_tree.addTopLevelItem(eq_item)
        self._bs_tree.expandAll()

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Report", "report.csv",
                                               "CSV Files (*.csv)")
        if not path:
            return
        try:
            idx = self._tabs.currentIndex()
            if idx == PNL_TAB:
                self._export_pnl_csv(path)
            elif idx == BS_TAB:
                self._export_bs_csv(path)
            else:
                self._tabs.currentWidget().export(path)
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _export_pnl_csv(self, path: str):
        """The P&L as on screen: the same columns, sub-accounts indented."""
        import csv
        from reports_export import pnl_grid_rows
        rep, buckets = self.pnl_report()
        labels = [b.label for b in buckets if b.key != RP.TOTAL] + ["Total"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Account"] + labels)
            for row in pnl_grid_rows(RP.pnl_lines(rep), rep["columns"]):
                w.writerow(row)

    def _run_business_bs(self, as_of: str):
        """Accountant layout: Assets = Liabilities + Equity, all positive."""
        from PyQt6.QtGui import QColor
        bs = self._balance_sheet(as_of)
        self._bs_tree.clear()
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        def _money(amt):
            amt = float(amt)
            return f"${amt:,.2f}" if amt >= 0 else f"(${abs(amt):,.2f})"

        def _bold(item, size_delta=0):
            f = item.font(0); f.setBold(True)
            if size_delta:
                f.setPointSize(f.pointSize() + size_delta)
            item.setFont(0, f); item.setFont(1, f)

        def _section(label, lines, total):
            root = QTreeWidgetItem([label, ""])
            for name, amt, meta in lines:
                item = QTreeWidgetItem([name, _money(amt)])
                item.setTextAlignment(1, right)
                if meta.get("plug"):
                    item.setForeground(0, QColor(WARNING))
                    item.setToolTip(0,
                        "Money that moved through non-P&L categories (such as loan "
                        "principal) without a matching balance-sheet entry, or a "
                        "transfer whose other half is missing. Record the loan "
                        "payment as a transfer to the loan account to clear it.")
                root.addChild(item)
            tot = QTreeWidgetItem([f"Total {label}", _money(total)])
            tot.setTextAlignment(1, right)
            _bold(tot)
            root.addChild(tot)
            root.setExpanded(True)
            self._bs_tree.addTopLevelItem(root)

        _section("Assets", bs["assets"], bs["total_assets"])
        _section("Liabilities", bs["liabilities"], bs["total_liabilities"])
        _section("Equity", bs["equity"], bs["total_equity"])
        both = bs["total_liabilities"] + bs["total_equity"]
        item = QTreeWidgetItem(["Total Liabilities & Equity", _money(both)])
        item.setTextAlignment(1, right)
        item.setForeground(1, QColor(SUCCESS if bs["balanced"] else DANGER))
        _bold(item, 1)
        self._bs_tree.addTopLevelItem(item)
        # Cash basis: open invoices/bills are memo items, not in the totals.
        t = self.db.ar_ap_totals(as_of)
        if t["ar"] or t["ap"]:
            memo = QTreeWidgetItem(["Memo (cash basis — not included above)", ""])
            for label, v in (("Accounts receivable (unpaid invoices)", t["ar"]),
                             ("Accounts payable (unpaid bills)", t["ap"])):
                if v:
                    m = QTreeWidgetItem([label, _money(v)])
                    m.setTextAlignment(1, right)
                    m.setForeground(0, QColor(MUTED))
                    m.setForeground(1, QColor(MUTED))
                    memo.addChild(m)
            self._bs_tree.addTopLevelItem(memo)
        self._bs_tree.expandAll()

    def _export_bs_csv(self, path: str):
        import csv
        as_of = self._date_to.date().toString("yyyy-MM-dd")
        bs = self._balance_sheet(as_of)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Section", "Line", "Amount"])
            for section, key, total_key in (("Assets", "assets", "total_assets"),
                                            ("Liabilities", "liabilities", "total_liabilities"),
                                            ("Equity", "equity", "total_equity")):
                for name, amt, _ in bs[key]:
                    w.writerow([section, name, f"{amt:.2f}"])
                w.writerow([section, f"Total {section}", f"{bs[total_key]:.2f}"])
