"""P&L and Balance Sheet reports."""
from datetime import date
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QLabel, QTabWidget, QComboBox,
                              QTreeWidget, QTreeWidgetItem, QHeaderView,
                              QFrame, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt, QDate
from ui.widgets import PageTitle, SecondaryButton, DateField, fmt_date
from ui.styles  import SUCCESS, DANGER, TEXT, MUTED, WARNING


class ReportsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
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

        run_btn = QPushButton("Run Report")
        run_btn.clicked.connect(self._run)
        filt.addWidget(run_btn)

        export_btn = SecondaryButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        filt.addWidget(export_btn)

        filt.addStretch()
        lay.addLayout(filt)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._run)

        # ── P&L tab ──
        pnl = QWidget()
        pnl_lay = QVBoxLayout(pnl)
        pnl_lay.setSpacing(8)

        self._pnl_summary = QLabel("")
        self._pnl_summary.setWordWrap(True)
        pnl_lay.addWidget(self._pnl_summary)

        self._pnl_tree = QTreeWidget()
        self._pnl_tree.setHeaderLabels(["Category", "Amount"])
        self._pnl_tree.setColumnWidth(0, 400)
        self._pnl_tree.setAlternatingRowColors(True)
        self._pnl_tree.header().setStretchLastSection(True)
        pnl_lay.addWidget(self._pnl_tree)
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

        lay.addWidget(self._tabs)

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
        if self._tabs.currentIndex() == 0:
            self._run_pnl()
        else:
            self._run_bs()

    def _run_pnl(self):
        start = self._date_from.date().toString("yyyy-MM-dd")
        end   = self._date_to.date().toString("yyyy-MM-dd")
        cls_filter = self._class_combo.currentData() or ""

        txns = self.db.get_transactions(start=start, end=end, include_transfers=False)
        if cls_filter:
            txns = [t for t in txns if t.get("class_id") == cls_filter]

        cats = {c["id"]: c for c in self.db.get_categories()}
        income_groups: dict[str, dict] = {}
        expense_groups: dict[str, dict] = {}
        debt_groups: dict[str, dict] = {}
        capital_groups: dict[str, dict] = {}

        for t in txns:
            amt = float(t.get("amount") or 0)
            cat = cats.get(t.get("category_id",""), {})
            cat_type = cat.get("type","expense")
            parent   = cats.get(cat.get("parent_id",""), {})
            group    = parent.get("name") or cat.get("name","Uncategorized")
            sub      = cat.get("name","") if cat.get("parent_id") else ""

            if cat_type == "income" or (not cat.get("id") and amt > 0):
                bucket = income_groups
            elif cat_type == "debt_repayment":
                bucket = debt_groups
            elif cat_type in ("asset", "inventory"):
                bucket = capital_groups
            else:
                bucket = expense_groups
            if group not in bucket:
                bucket[group] = {"total": 0.0, "subs": {}}
            bucket[group]["total"] += amt
            if sub:
                bucket[group]["subs"][sub] = bucket[group]["subs"].get(sub, 0) + amt

        # Non-cash lines (depreciation, gains on asset sales) — company-wide, so
        # they're left out when filtering to a single class.
        if not cls_filter:
            for ln in self.db.computed_pnl_lines(start, end):
                bucket = income_groups if ln["section"] == "income" else expense_groups
                g = bucket.setdefault(ln["group"], {"total": 0.0, "subs": {}})
                g["total"] += float(ln["amount"])
                if ln["name"]:
                    g["subs"][ln["name"]] = g["subs"].get(ln["name"], 0) + float(ln["amount"])

        total_income   = sum(g["total"] for g in income_groups.values())
        total_expenses = sum(g["total"] for g in expense_groups.values())
        total_debt     = sum(g["total"] for g in debt_groups.values())
        # Debt repayment is principal moving between balances, not a P&L expense,
        # so it is deliberately excluded from Net Income.
        net = total_income + total_expenses

        self._pnl_tree.clear()

        def _money(amt: float) -> str:
            return f"${amt:,.2f}" if amt >= 0 else f"(${abs(amt):,.2f})"

        def _add_group(label: str, groups: dict, sign: int = 1):
            root = QTreeWidgetItem([label, ""])
            root.setExpanded(True)
            for group, data in sorted(groups.items()):
                item = QTreeWidgetItem([group, _money(data["total"] * sign)])
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                color = SUCCESS if data["total"] * sign >= 0 else DANGER
                item.setForeground(1, __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color))
                for sub, sub_amt in sorted(data["subs"].items()):
                    sub_item = QTreeWidgetItem([f"    ↳ {sub}", _money(sub_amt * sign)])
                    sub_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    item.addChild(sub_item)
                root.addChild(item)
            total_item = QTreeWidgetItem([f"Total {label}", _money(
                sum(g["total"] for g in groups.values()) * sign)])
            total_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            font = total_item.font(0); font.setBold(True); total_item.setFont(0, font); total_item.setFont(1, font)
            root.addChild(total_item)
            return root

        income_root = _add_group("Income", income_groups, 1)
        self._pnl_tree.addTopLevelItem(income_root)
        income_root.setExpanded(True)

        exp_root = _add_group("Expenses", expense_groups, -1)
        self._pnl_tree.addTopLevelItem(exp_root)
        exp_root.setExpanded(True)

        net_item = QTreeWidgetItem(["Net Income", _money(net)])
        net_item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        from PyQt6.QtGui import QColor, QFont as QF
        net_item.setForeground(1, QColor(SUCCESS if net >= 0 else DANGER))
        f = net_item.font(0); f.setBold(True); f.setPointSize(f.pointSize()+1)
        net_item.setFont(0, f); net_item.setFont(1, f)
        self._pnl_tree.addTopLevelItem(net_item)

        # Debt repayment shown separately below Net Income (does not affect it).
        if debt_groups:
            debt_root = _add_group("Debt Repayment", debt_groups, -1)
            self._pnl_tree.addTopLevelItem(debt_root)
            debt_root.setExpanded(True)
        # Fixed-asset / inventory purchases are balance-sheet items: they reach
        # the P&L later as depreciation or cost of goods sold.
        if capital_groups:
            cap_root = _add_group("Asset & Inventory Purchases (on Balance Sheet)",
                                  capital_groups, -1)
            self._pnl_tree.addTopLevelItem(cap_root)
            cap_root.setExpanded(True)

        color = SUCCESS if net >= 0 else DANGER
        debt_html = (f" &nbsp;&nbsp; <b>Debt Repayment:</b> "
                     f"<span style='color:{DANGER}'>${abs(total_debt):,.2f}</span>"
                     if debt_groups else "")
        self._pnl_summary.setText(
            f"<b>Period:</b> {fmt_date(start)} to {fmt_date(end)} &nbsp;&nbsp; "
            f"<b>Income:</b> <span style='color:{SUCCESS}'>${total_income:,.2f}</span> &nbsp;&nbsp; "
            f"<b>Expenses:</b> <span style='color:{DANGER}'>${abs(total_expenses):,.2f}</span> &nbsp;&nbsp; "
            f"<b>Net:</b> <span style='color:{color}'>${net:,.2f}</span>"
            f"{debt_html}"
        )
        self._pnl_summary.setTextFormat(Qt.TextFormat.RichText)

    def _balance_sheet(self, as_of: str) -> dict:
        """Balance sheet data from the shared, tested ledger math."""
        from utils.ledger import balance_sheet
        cats = {c["id"]: c for c in self.db.get_categories()}
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
            if self._tabs.currentIndex() == 0:
                self._export_pnl_csv(path)
            else:
                self._export_bs_csv(path)
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _export_pnl_csv(self, path: str):
        import csv
        start = self._date_from.date().toString("yyyy-MM-dd")
        end   = self._date_to.date().toString("yyyy-MM-dd")
        txns  = self.db.get_transactions(start=start, end=end, include_transfers=False)
        cats  = {c["id"]: c for c in self.db.get_categories()}
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Account", "Payee", "Category", "Class", "Amount"])
            accts = {a["id"]: a for a in self.db.get_accounts()}
            clsmap = {c["id"]: c for c in self.db.get_classes()}
            for t in txns:
                cat = cats.get(t.get("category_id",""), {})
                parent = cats.get(cat.get("parent_id",""), {})
                cat_name = f"{parent.get('name','')} > {cat.get('name','')}" if parent else cat.get("name","")
                w.writerow([t.get("date",""), accts.get(t["account_id"],{}).get("name",""),
                            t.get("payee",""), cat_name,
                            clsmap.get(t.get("class_id",""),{}).get("name",""),
                            t.get("amount","")])
            for ln in self.db.computed_pnl_lines(start, end):
                label = ln["group"] + (f" > {ln['name']}" if ln["name"] else "")
                w.writerow([end, "(computed)", "Non-cash: " + ln["computed"], label, "",
                            f"{ln['amount']:.2f}"])

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
