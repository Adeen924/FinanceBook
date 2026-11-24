from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QComboBox, QLineEdit, QMessageBox, QCheckBox,
                              QLabel, QHeaderView, QMenu, QDialog, QDialogButtonBox)
from PyQt6.QtCore import Qt, QDate, QTimer
from PyQt6.QtGui import QAction
from ui.widgets import (PageTitle, DataTable, SecondaryButton, DangerButton,
                        DateField, fmt_date)
from ui.styles  import SUCCESS, DANGER, WARNING


class FindTransfersDialog(QDialog):
    """Transfers already in the books: pairs the matching engine thinks are one
    transfer (Potential Transfers → Link) and the linked ones (Unlink). The
    TransfersPage widgets, shown from the Transactions page (v1.18)."""
    def __init__(self, db, parent=None):
        super().__init__(parent)
        from ui.pages.transfers import TransfersPage
        self.setWindowTitle("Find Transfers")
        self.resize(1000, 620)
        lay = QVBoxLayout(self)
        self.transfers = TransfersPage(db, self, embedded=True)
        lay.addWidget(self.transfers, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)
        self.transfers.refresh()




def split_lines_text(db, row: dict, cats=None, accts=None, classes=None) -> list[str]:
    """One "Account · memo · class · $amount" text per line of a split row."""
    cats = cats if cats is not None else db.category_map()
    accts = accts if accts is not None else {a["id"]: a for a in db.get_accounts(active=None)}
    classes = classes if classes is not None else {c["id"]: c for c in db.get_classes()}
    others = db.transfer_partner_accounts(row.get("lines") or [])
    out = []
    for l in row.get("lines") or []:
        if l.get("id") in others:
            name = "→ " + accts.get(others[l["id"]], {}).get("name", "another account")
        else:
            cat = cats.get(l.get("category_id", ""), {})
            parent = cats.get(cat.get("parent_id", ""), {})
            name = (f"{parent['name']} → {cat['name']}" if parent
                    else cat.get("name", "")) or "Uncategorized"
        bits = [name]
        if l.get("memo"):
            bits.append(l["memo"])
        if l.get("class_id") and l["class_id"] in classes:
            bits.append(f"class {classes[l['class_id']].get('name', '')}")
        amt = float(l.get("amount") or 0)
        bits.append(f"${amt:,.2f}" if amt >= 0 else f"(${abs(amt):,.2f})")
        out.append("  ·  ".join(bits))
    return out


class TransactionsPage(QWidget):
    """The register. One row per transaction: a split (several account lines
    under one bank entry) is ONE row showing "-Split- (N)" and its total —
    see Database.list_register_rows."""
    # Category-filter value for "⚠ Uncategorized only".
    UNCATEGORIZED = "__uncategorized__"
    # Typing in the search box (or a date) re-filters after this pause.
    FILTER_DELAY_MS = 300
    SPLIT_LABEL = "-Split-"

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        # A filter set from a report (show_filtered): {"category_ids", "class_id",
        # "contact_id", "payee", "label"}; shown as a chip with ✕.
        self._extra: dict = {}
        self._build()

    # Fields the register can be sorted by (key → menu label). Balance and the
    # reconciled flag are intentionally not sortable.
    _SORT_FIELDS = [("date", "Date"), ("account", "Account"),
                    ("payee", "Payee / Memo"), ("category", "Account")]

    def _build(self):
        # Sort state. Default to Date ascending so the newest transaction sits at
        # the bottom of the register (QuickBooks-style — see _sort_and_render).
        self._sort_key = "date"
        self._sort_asc = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Transactions"))
        hdr.addStretch()

        self._sort_btn = QPushButton("Sort: Date ↑")
        self._sort_btn.setObjectName("Secondary")
        self._sort_btn.setProperty("hasMenu", True)
        self._build_sort_menu()
        hdr.addWidget(self._sort_btn)

        self._find_transfers_btn = QPushButton("Find Transfers…")
        self._find_transfers_btn.setObjectName("Secondary")
        self._find_transfers_btn.setToolTip(
            "Find pairs already in your books that are one transfer between your own "
            "accounts (same amount, opposite direction, ≤3 days apart) and link them; "
            "see or unlink linked transfers.")
        self._find_transfers_btn.clicked.connect(self._find_transfers)
        hdr.addWidget(self._find_transfers_btn)

        self._add_btn = QPushButton("+ Add Transaction")
        self._add_btn.clicked.connect(self._add)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        # ── Filter bar ──
        filt = QHBoxLayout()
        filt.setSpacing(8)

        self._acct_filter = QComboBox()
        self._acct_filter.setMinimumWidth(160)
        self._acct_filter.addItem("All Accounts", "")
        filt.addWidget(self._acct_filter)

        self._cat_filter = QComboBox()
        self._cat_filter.setMinimumWidth(160)
        self._cat_filter.addItem("All Income / Expense Accounts", "")
        self._cat_filter.setToolTip("Show only entries filed under one income or expense account")
        filt.addWidget(self._cat_filter)

        filt.addWidget(QLabel("From:"))
        self._date_from = DateField()
        self._date_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        filt.addWidget(self._date_from)

        filt.addWidget(QLabel("To:"))
        # Blank = no upper limit (future-dated entries show too).
        self._date_to = DateField(allow_empty=True)
        self._date_to.clear()
        self._date_to.setToolTip("Leave blank for no end date")
        filt.addWidget(self._date_to)
        filt.addStretch()
        lay.addLayout(filt)

        # Second row: search + actions (one row is too wide below ~1500px).
        filt = QHBoxLayout()
        filt.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search payee / memo…")
        self._search_edit.setMinimumWidth(180)
        filt.addWidget(self._search_edit, 1)

        self._show_transfers = QCheckBox("Show transfers")
        # Transfers are still real entries on the account, so show them in the
        # register by default (they're only excluded from P&L / reports).
        self._show_transfers.setChecked(True)
        filt.addWidget(self._show_transfers)

        clear_btn = SecondaryButton("Clear")
        clear_btn.clicked.connect(self._clear_filter)
        filt.addWidget(clear_btn)

        lay.addLayout(filt)

        # "Filtered from report: … ✕" — a filter a report line set (show_filtered).
        self._chip = QWidget()
        self._chip.setObjectName("FilterChip")
        self._chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QHBoxLayout(self._chip)
        cl.setContentsMargins(10, 4, 4, 4)
        cl.setSpacing(6)
        self._chip_label = QLabel("")
        self._chip_label.setWordWrap(True)
        cl.addWidget(self._chip_label, 1)
        self._chip_close = QPushButton("✕")
        self._chip_close.setObjectName("Secondary")
        self._chip_close.setFixedSize(28, 24)
        self._chip_close.setToolTip("Remove this filter")
        self._chip_close.clicked.connect(self.clear_report_filter)
        cl.addWidget(self._chip_close)
        self._chip.setStyleSheet("#FilterChip { background: rgba(37,99,235,0.10); "
                                 "border: 1px solid rgba(37,99,235,0.35); border-radius: 8px; }")
        self._chip.setVisible(False)
        lay.addWidget(self._chip)

        # Every filter control re-filters by itself. Combos and the checkbox act
        # at once; the search box and dates wait for a short pause in typing.
        # _quiet is set while several controls are changed together (Clear,
        # opening an account), which then reload once.
        self._quiet = False
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(self.FILTER_DELAY_MS)
        self._filter_timer.timeout.connect(self._on_filter_changed)
        self._acct_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._cat_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._show_transfers.toggled.connect(self._on_filter_changed)
        self._date_from.dateChanged.connect(self._schedule_filter)
        self._date_to.dateChanged.connect(self._schedule_filter)
        self._search_edit.textChanged.connect(self._schedule_filter)

        # ── Summary bar ──
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("Muted")
        lay.addWidget(self._summary_label)

        # ── Table ──
        self._table = DataTable(
            ["Date", "Bank / Card", "Payee / Memo", "Account", "Amount", "Balance", "R", ""])
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(1, 140)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(3, 240)
        self._table.setColumnWidth(4, 110)
        self._table.setColumnWidth(5, 120)
        self._table.setColumnWidth(6, 36)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(7, 84)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.doubleClicked.connect(self._edit_selected)
        self._table.itemSelectionChanged.connect(self._update_detail)
        lay.addWidget(self._table)

        # Detail strip: the lines of the selected split.
        self._detail = QLabel("")
        self._detail.setObjectName("Muted")
        self._detail.setWordWrap(True)
        self._detail.setTextFormat(Qt.TextFormat.RichText)
        self._detail.setVisible(False)
        lay.addWidget(self._detail)

        self._txns: list[dict] = []

    def refresh(self):
        accounts = self.db.get_accounts()
        cats = self.db.get_categories_for_book()

        # Repopulate filter combos
        self._acct_filter.blockSignals(True)
        cur_acct = self._acct_filter.currentData()
        self._acct_filter.clear()
        self._acct_filter.addItem("All Accounts", "")
        for a in accounts:
            self._acct_filter.addItem(a["name"], a["id"])
        for i in range(self._acct_filter.count()):
            if self._acct_filter.itemData(i) == cur_acct:
                self._acct_filter.setCurrentIndex(i)
                break
        self._acct_filter.blockSignals(False)

        self._cat_filter.blockSignals(True)
        cur_cat = self._cat_filter.currentData()
        self._cat_filter.clear()
        self._cat_filter.addItem("All Income / Expense Accounts", "")
        self._cat_filter.addItem("⚠ Uncategorized only", self.UNCATEGORIZED)
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            self._cat_filter.addItem(cat["name"], cat["id"])
            for sub in cats:
                if sub.get("parent_id") == cat["id"]:
                    self._cat_filter.addItem(f"    ↳ {sub['name']}", sub["id"])
        for i in range(self._cat_filter.count()):
            if self._cat_filter.itemData(i) == cur_cat:
                self._cat_filter.setCurrentIndex(i)
                break
        self._cat_filter.blockSignals(False)

        self._apply_filter()

    def _schedule_filter(self, *_):
        if not self._quiet:
            self._filter_timer.start()

    def _on_filter_changed(self, *_):
        if not self._quiet:
            self._apply_filter()

    def _select_account(self, account_id: str):
        ix = self._acct_filter.findData(account_id or "")
        if ix < 0 and account_id:
            # An inactive account (e.g. from a report): offer it in the filter.
            acct = self.db.get_account(account_id)
            if acct:
                self._acct_filter.addItem(acct.get("name", ""), account_id)
                ix = self._acct_filter.count() - 1
        self._acct_filter.setCurrentIndex(max(ix, 0))

    def show_uncategorized(self):
        """Show every entry that still needs an account (Dashboard alert)."""
        self._quiet = True
        try:
            self._set_extra({})
            self._acct_filter.setCurrentIndex(0)
            self._search_edit.clear()
            self._date_from.setDate(QDate(2000, 1, 1))
            self._date_to.clear()
            ix = self._cat_filter.findData(self.UNCATEGORIZED)
            self._cat_filter.setCurrentIndex(max(ix, 0))
        finally:
            self._quiet = False
        self._apply_filter()

    def show_filtered(self, category_ids=None, account_id=None, start=None, end=None,
                      class_id=None, contact_id=None, payee=None, label: str = "",
                      transfers: bool = True):
        """Open the register filtered the way a report line was (drill-down):
        its accounts (category_ids — the line and its sub-accounts), a bank /
        balance-sheet account, the date range (no start = from the beginning,
        no end = no limit), a class and a payee (contact id, or payee text).
        Shows a "Filtered from report: <label> ✕" chip. Splits appear when any
        line matches, showing the matching lines' amount. transfers=False
        (P&L lines) leaves transfers out, as the report does."""
        self._quiet = True
        try:
            self._search_edit.clear()
            self._cat_filter.setCurrentIndex(0)
            self._show_transfers.setChecked(bool(transfers))
            self._select_account(account_id or "")
            self._date_from.setDate(QDate.fromString(start, "yyyy-MM-dd") if start
                                    else QDate(1900, 1, 1))
            if end:
                self._date_to.setDate(QDate.fromString(end, "yyyy-MM-dd"))
            else:
                self._date_to.clear()
            self._set_extra({"category_ids": (set(category_ids)
                                              if category_ids is not None else None),
                             "class_id": class_id or "", "contact_id": contact_id or "",
                             "payee": payee or "", "label": label or "report line"})
        finally:
            self._quiet = False
        self._apply_filter()

    def _set_extra(self, extra: dict):
        import html
        self._extra = dict(extra or {})
        on = bool(self._extra)
        self._chip.setVisible(on)
        self._chip_label.setText(
            f"Filtered from report: <b>{html.escape(self._extra.get('label', ''))}</b>"
            if on else "")

    def clear_report_filter(self):
        self._set_extra({})
        self._apply_filter()

    def _filter_args(self) -> dict:
        cat_id = self._cat_filter.currentData() or None
        cat_ids = None
        if cat_id and cat_id != self.UNCATEGORIZED:
            cat_ids = {cat_id}
        extra_cats = self._extra.get("category_ids")
        if extra_cats is not None:
            cat_ids = (cat_ids & extra_cats) if cat_ids else set(extra_cats)
        return dict(
            account_id=self._acct_filter.currentData() or None,
            start=self._date_from.date().toString("yyyy-MM-dd"),
            end=self._date_to.iso() or None,
            include_transfers=self._show_transfers.isChecked(),
            category_ids=cat_ids,
            uncategorized=cat_id == self.UNCATEGORIZED,
            search=self._search_edit.text(),
            class_id=self._extra.get("class_id", ""),
            contact_id=self._extra.get("contact_id", ""),
            payee=self._extra.get("payee", ""))

    def _apply_filter(self, *_args, keep=None):
        """Reload the register. keep: a transaction (dict) to re-select and
        scroll to afterwards — the one just edited / split / re-filed — found
        by id (or any of its lines), else its split group or transfer pair,
        else same account, date and payee."""
        self._filter_timer.stop()       # this reload covers any pending one
        args = self._filter_args()
        if args["category_ids"] is not None and not args["category_ids"]:
            self._txns = []     # the category filter and a report filter disagree
        else:
            self._txns = self.db.list_register_rows(**args)
        self._sort_and_render(keep=keep)

    # ── transfers ─────────────────────────────────────────────────────────────

    def make_find_transfers_dialog(self) -> FindTransfersDialog:
        return FindTransfersDialog(self.db, self)

    def _find_transfers(self):
        dlg = self.make_find_transfers_dialog()
        dlg.exec()
        changed = dlg.transfers.links_changed
        dlg.deleteLater()
        if changed:
            self._apply_filter()

    # ── sorting ───────────────────────────────────────────────────────────────

    def _build_sort_menu(self):
        menu = QMenu(self)
        self._sort_actions = {}
        for key, label in self._SORT_FIELDS:
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=key: self._set_sort(k))
            menu.addAction(act)
            self._sort_actions[key] = act
        self._sort_btn.setMenu(menu)
        self._update_sort_ui()

    def _set_sort(self, key: str):
        # Clicking the current field flips direction; a new field starts
        # ascending (Date ascending = newest at bottom; text fields = A→Z).
        if key == self._sort_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key = key
            self._sort_asc = True
        self._update_sort_ui()
        self._sort_and_render()

    def _update_sort_ui(self):
        arrow  = "↑" if self._sort_asc else "↓"
        labels = dict(self._SORT_FIELDS)
        self._sort_btn.setText(f"Sort: {labels[self._sort_key]} {arrow}")
        for key, act in self._sort_actions.items():
            act.setText(f"{labels[key]}  {arrow}" if key == self._sort_key
                        else labels[key])

    def _sort_txns(self):
        cats  = self.db.category_map()
        accts = {a["id"]: a for a in self.db.get_accounts(active=None)}

        def acct_name(t):
            return accts.get(t.get("account_id", ""), {}).get("name", "").lower()

        def payee_name(t):
            return (t.get("payee") or t.get("memo") or "").lower()

        def cat_name(t):
            if t.get("is_split"):
                return self.SPLIT_LABEL.lower()
            cat    = cats.get(t.get("category_id", ""), {})
            parent = cats.get(cat.get("parent_id", ""), {})
            base   = (f"{parent['name']} {cat.get('name','')}" if parent
                      else cat.get("name", ""))
            return base.strip().lower()

        # Each key falls back to date/created_at so ties stay in stable
        # chronological order.
        keyfns = {
            "date":     lambda t: (t.get("date", ""), t.get("created_at", "")),
            "account":  lambda t: (acct_name(t), t.get("date", ""), t.get("created_at", "")),
            "payee":    lambda t: (payee_name(t), t.get("date", ""), t.get("created_at", "")),
            "category": lambda t: (cat_name(t), t.get("date", ""), t.get("created_at", "")),
        }
        fn = keyfns.get(self._sort_key, keyfns["date"])
        self._txns.sort(key=fn, reverse=not self._sort_asc)

    def _find_row(self, keep) -> int:
        if not keep:
            return -1
        keep_ids = (set(keep.get("line_ids") or []) | {keep.get("id")}) - {None, ""}
        tests = [lambda t: keep.get("id") and t.get("id") == keep["id"],
                 lambda t: keep_ids & set(t.get("line_ids") or [t.get("id")]),
                 lambda t: keep.get("split_group_id")
                 and t.get("split_group_id") == keep["split_group_id"],
                 lambda t: keep.get("transfer_pair_id")
                 and t.get("transfer_pair_id") == keep["transfer_pair_id"]
                 and t.get("account_id") == keep.get("account_id"),
                 lambda t: (t.get("account_id"), t.get("date"), t.get("payee") or "")
                 == (keep.get("account_id"), keep.get("date"), keep.get("payee") or "")]
        for test in tests:
            for row, t in enumerate(self._txns):
                if test(t):
                    return row
        return -1

    def _sort_and_render(self, keep=None):
        self._sort_txns()
        self._render_table()
        row = self._find_row(keep)
        if row >= 0:
            # Stay on the transaction the user just worked on.
            self._table.selectRow(row)
            self._table.scrollTo(self._table.model().index(row, 0),
                                 self._table.ScrollHint.PositionAtCenter)
            return
        # In the default register view (Date ascending) the newest row is at the
        # bottom, so open scrolled there — matches how QuickBooks shows a
        # register. Any other sort reads top-down, so start at the top.
        if self._sort_key == "date" and self._sort_asc:
            self._table.scrollToBottom()
        else:
            self._table.scrollToTop()

    def _clear_filter(self):
        self._quiet = True
        try:
            self._set_extra({})
            self._acct_filter.setCurrentIndex(0)
            self._cat_filter.setCurrentIndex(0)
            self._date_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
            self._date_to.clear()
            self._search_edit.clear()
            self._show_transfers.setChecked(True)
        finally:
            self._quiet = False
        self._apply_filter()

    def show_account(self, account_id: str):
        """Filter the page to one account, newest first — used when an account
        name is clicked on the Accounts or Dashboard page."""
        self._quiet = True
        try:
            self._set_extra({})
            self._cat_filter.setCurrentIndex(0)
            self._search_edit.clear()
            self._show_transfers.setChecked(True)
            # Widen the date range so recent transactions show regardless of year.
            self._date_from.setDate(QDate(2000, 1, 1))
            self._date_to.clear()
            self._select_account(account_id)
        finally:
            self._quiet = False
        self._apply_filter()

    def _transfer_targets(self) -> dict:
        """{transaction id: the other account's id} for every transfer row on
        screen, so the Account column shows where the money went."""
        return self.db.transfer_partner_accounts(
            [t for t in self._txns if not t.get("is_split")])

    @staticmethod
    def shown_amount(t: dict) -> float:
        """What the register shows for a row: a split's matching lines when a
        line filter is on, else the transaction's amount (a split's total)."""
        return float(t.get("match_amount", t.get("amount")) or 0)

    def _render_table(self):
        cats   = self.db.category_map()
        accts  = {a["id"]: a for a in self.db.get_accounts(active=None)}
        classes = {c["id"]: c for c in self.db.get_classes()}
        others = self._transfer_targets()

        self._table.clear_rows()
        self._table.setRowCount(len(self._txns))
        total = 0.0

        # Running balance only makes sense for a single account, so it's shown
        # when one account is selected in the filter (blank under "All
        # Accounts"). Balances are the true account balance after each txn,
        # computed over the account's full history — see running_balances().
        # A split is one step.
        acct_id = self._acct_filter.currentData() or None
        balances = self.db.running_balances(acct_id) if acct_id else {}
        att_ids, att_groups = self.db.attachment_index()

        for row, t in enumerate(self._txns):
            is_split = bool(t.get("is_split"))
            tip = ""
            if is_split:
                n = int(t.get("line_count") or len(t.get("lines") or []))
                cat_name = f"{self.SPLIT_LABEL} ({n})"
                lines = split_lines_text(self.db, t, cats, accts, classes)
                full = float(t.get("amount") or 0)
                tip = (f"Split into {n} lines — total ${abs(full):,.2f}\n"
                       + "\n".join("• " + x for x in lines))
            else:
                cat = cats.get(t.get("category_id",""), {})
                parent = cats.get(cat.get("parent_id",""), {})
                cat_name = f"{parent['name']} → {cat['name']}" if parent else cat.get("name","")
                if t.get("id") in others:
                    cat_name = "⇄ " + accts.get(others[t["id"]], {}).get("name", "another account")
            acct_name = accts.get(t.get("account_id",""), {}).get("name","—")
            payee = t.get("payee") or t.get("memo") or "—"
            amt = self.shown_amount(t)
            total += amt

            if str(t.get("is_transfer","0")) == "1" and not is_split:
                disp_payee = f"[Transfer] {payee}"
            else:
                disp_payee = payee
            if t.get("split_source"):
                src_acct, src_payee = t["split_source"]
                part = (f"Part of a split from "
                        f"{accts.get(src_acct, {}).get('name', 'another account')}"
                        + (f" · {src_payee}" if src_payee else ""))
                disp_payee = f"{disp_payee}  ({part})"
                tip = part
            if (set(t.get("line_ids") or [t.get("id")]) & att_ids
                    or (t.get("split_group_id") or "-") in att_groups):
                disp_payee = "📎 " + disp_payee

            self._table.set_item(row, 0, fmt_date(t.get("date", "")))
            self._table.set_item(row, 1, acct_name)
            self._table.set_item(row, 2, disp_payee)
            if cat_name:
                self._table.set_item(row, 3, cat_name)
            else:
                self._table.set_item(row, 3, "⚠ Uncategorized",
                                      color=WARNING)
            self._table.money_item(row, 4, amt)
            if is_split and "match_amount" in t:
                tip += (f"\n\nShowing the matching line(s): ${abs(amt):,.2f} of the "
                        f"${abs(float(t.get('amount') or 0)):,.2f} total.")
            if tip:
                for col in (2, 3, 4):
                    it = self._table.item(row, col)
                    if it is not None:
                        it.setToolTip(tip)

            # Running balance (single-account view only).
            bal = balances.get(t.get("id"))
            if bal is not None:
                bal_text = (f"${bal:,.2f}" if bal >= 0
                            else f"(${abs(bal):,.2f})")
                self._table.set_item(row, 5, bal_text,
                    align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    color=(DANGER if bal < 0 else None), bold=True)

            if str(t.get("reconciled","0")) == "1":
                self._table.set_item(row, 6, "✓",
                    align=Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                    color=SUCCESS)

            from PyQt6.QtWidgets import QWidget as _W, QHBoxLayout as _HL
            _cell = _W(); _cl = _HL(_cell); _cl.setContentsMargins(4, 3, 4, 3)
            _edit_btn = QPushButton("Edit")
            _edit_btn.setObjectName("Secondary")
            _edit_btn.setFixedSize(56, 26)
            _edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _edit_btn.clicked.connect(lambda _, txn=t: self._edit_txn(txn))
            _cl.addStretch()
            _cl.addWidget(_edit_btn)
            _cl.addStretch()
            self._table.setCellWidget(row, 7, _cell)

            self._table.setRowHeight(row, 38)

        amts = [self.shown_amount(t) for t in self._txns]
        income  = sum(a for a in amts if a > 0)
        expense = sum(a for a in amts if a < 0)
        self._summary_label.setText(
            f"{len(self._txns)} transactions   |   "
            f"Income: ${income:,.2f}   |   "
            f"Expenses: (${abs(expense):,.2f})   |   "
            f"Net: ${total:,.2f}"
        )
        self._update_detail()

    def _update_detail(self):
        row = self._table.currentRow()
        t = self._txns[row] if 0 <= row < len(self._txns) else None
        if not t or not t.get("is_split"):
            self._detail.setVisible(False)
            return
        import html
        lines = split_lines_text(self.db, t)
        self._detail.setText(
            f"<b>{self.SPLIT_LABEL} {html.escape(t.get('payee') or '')}</b> — "
            + " &nbsp;|&nbsp; ".join(html.escape(x) for x in lines))
        self._detail.setVisible(True)

    def _add(self, *_args, start_split: bool = False):
        """New transaction; with one bank / card account picked in the filter,
        it starts in that account."""
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, account_id=self._acct_filter.currentData() or "",
                                parent=self, start_split=start_split)
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            # A split or transfer is already persisted by the dialog.
            data = dlg.get_data()
            if not getattr(dlg, "handled", False):
                data = self.db.save_transaction(data)
            self._apply_filter(keep=getattr(dlg, "result_txn", None) or data)

    def _edit_selected(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._txns):
            return
        self._edit_txn(self._txns[row])

    def _open_target(self, txn: dict) -> dict:
        """The transaction to open for a register row: the whole split for a
        split (or for the other side of a split line in another account)."""
        if txn.get("split_source") and txn.get("transfer_pair_id"):
            src = self.db.get_transfer_partner(txn)
            if src and src.get("split_group_id"):
                full = self.db.get_transaction_with_lines(src["id"])
                if full:
                    return full["header"]
        if txn.get("split_group_id") and not txn.get("lines"):
            full = self.db.get_transaction_with_lines(txn["id"])
            if full:
                return full["header"]
        return txn

    def _edit_txn(self, txn, start_split: bool = False):
        from ui.dialogs.transaction_dialog import TransactionDialog
        target = self._open_target(txn)
        dlg = TransactionDialog(self.db, transaction=target, parent=self,
                                start_split=start_split)
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            # A split or transfer is already persisted by the dialog — don't
            # re-save the (now-replaced) original on top of it.
            if getattr(dlg, "deleted", False):
                self._apply_filter()
                return
            data = dlg.get_data()
            if not getattr(dlg, "handled", False):
                data = self.db.save_transaction(data)
            self._apply_filter(keep=getattr(dlg, "result_txn", None) or data)

    def _split_txn(self, txn):
        """Split… / Edit Split…: the Transaction window in Split mode."""
        if (str(txn.get("is_transfer", "0")) == "1" and not txn.get("is_split")
                and not txn.get("split_source")):
            QMessageBox.information(self, "Cannot Split",
                "Transfers can't be split. Remove the transfer link first.")
            return
        if txn.get("loan_id"):
            QMessageBox.information(self, "Cannot Split",
                "Loan payments can't be split — they already track principal "
                "and interest separately.")
            return
        self._edit_txn(txn, start_split=True)

    def _context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._txns):
            return
        txn = self._txns[row]
        is_split = bool(txn.get("is_split"))
        menu = QMenu(self)

        edit_act = QAction("Edit", self)
        edit_act.triggered.connect(lambda: self._edit_txn(txn))
        menu.addAction(edit_act)

        split_label = "Edit Split…" if is_split else "Split…"
        split_act = QAction(split_label, self)
        split_act.triggered.connect(lambda: self._split_txn(txn))
        menu.addAction(split_act)
        if is_split:
            rm_act = QAction("Remove Split…", self)
            rm_act.setToolTip("Make this one transaction filed under one account again.")
            rm_act.triggered.connect(lambda: self._remove_split(txn))
            menu.addAction(rm_act)

        # Quick "Set Account" submenu: one submenu per account group. On a
        # split it replaces the lines with that one account (Remove Split).
        from utils.chart_of_accounts import picker_entries
        cat_menu = menu.addMenu("Set Account (replaces the split)" if is_split
                                else "Set Account")
        for label, opts in picker_entries(
                self.db.get_categories_for_book(keep_ids=[txn.get("category_id", "")]),
                self.db.get_accounts(), exclude_account_id=txn.get("account_id", "")):
            sub_menu = cat_menu.addMenu(label)
            for text, value in opts:
                act = QAction(text, self)
                act.triggered.connect(lambda _, v=value: self._set_category(txn, v))
                sub_menu.addAction(act)

        if (self.db.is_business() and str(txn.get("is_transfer", "0")) != "1"
                and not is_split):
            fa_act = QAction("Add as Fixed Asset…", self)
            fa_act.triggered.connect(lambda: self._add_as_asset(txn))
            menu.addAction(fa_act)

        menu.addSeparator()
        att_act = QAction("Attach Receipt or File…", self)
        att_act.triggered.connect(lambda: self._attach_file(txn))
        menu.addAction(att_act)
        receipts = self.db.get_transaction_receipts(txn)
        if receipts:
            open_menu = menu.addMenu("Open Attachment")
            for rc in receipts:
                a = QAction(rc.get("original_name") or rc.get("vendor") or "Receipt", self)
                a.triggered.connect(lambda _, r=rc: self._open_receipt(r))
                open_menu.addAction(a)
            item_act = QAction("Itemize from Receipt…", self)
            item_act.triggered.connect(lambda: self._itemize(txn, receipts[0]))
            menu.addAction(item_act)

        if (str(txn.get("is_transfer", "0")) == "1" and txn.get("transfer_pair_id")
                and not txn.get("split_group_id") and not txn.get("split_source")):
            menu.addSeparator()
            unlink_act = QAction("Unlink Transfer", self)
            unlink_act.setToolTip("Split this transfer back into two ordinary transactions.")
            unlink_act.triggered.connect(lambda: self._unlink_transfer(txn))
            menu.addAction(unlink_act)

        menu.addSeparator()
        del_act = QAction("Delete", self)
        del_act.triggered.connect(lambda: self._delete_txn(txn))
        menu.addAction(del_act)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _attach_file(self, txn):
        import os
        from PyQt6.QtWidgets import QFileDialog
        from ui.pages.receipts import FILE_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Attach Receipt or File",
                                              os.path.expanduser("~"), FILE_FILTER)
        if not path:
            return
        try:
            rc, _dup = self.db.add_receipt_file(path)
        except OSError as e:
            QMessageBox.warning(self, "Attach Failed", str(e))
            return
        if not rc.get("vendor"):
            self.db.save_receipt({"id": rc["id"], "vendor": txn.get("payee", ""),
                                  "receipt_date": txn.get("date", ""),
                                  "total": abs(float(txn.get("amount") or 0))})
        # A split's attachment belongs to the whole transaction (its group).
        self.db.attach_receipt(rc["id"], self._open_target(txn))
        self._apply_filter(keep=txn)

    def _add_as_asset(self, txn):
        from ui.dialogs.asset_dialog import AssetDialog
        dlg = AssetDialog(self.db, parent=self, preset={
            "name": txn.get("memo") or txn.get("payee") or "", "vendor": txn.get("payee", ""),
            "purchase_date": txn.get("date", ""), "in_service_date": txn.get("date", ""),
            "cost": abs(float(txn.get("amount") or 0)), "purchase_txn_id": txn.get("id", "")})
        if dlg.exec() != AssetDialog.DialogCode.Accepted:
            return
        data = dlg.get_data()
        self.db.save_asset(data)
        cat = self.db.get_category(txn.get("category_id", "")) or {}
        if cat.get("type") != "asset" and QMessageBox.question(
                self, "Move Purchase to the Balance Sheet?",
                "So the cost isn't expensed twice (once now, again as depreciation), "
                "re-file this purchase under “Fixed Assets (purchases & sales)”?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            txn = self.db.get_transaction(txn["id"]) or txn
            txn["category_id"] = self.db.fixed_asset_category_id(
                data.get("asset_class") == "prepaid")
            self.db.save_transaction(txn)
        self._apply_filter(keep=txn)

    def _open_receipt(self, rc):
        import os
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        path = self.db.receipt_abs_path(rc)
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, "Missing File",
                                    "The attached file isn't in the receipts folder anymore.")

    def _itemize(self, txn, rc):
        """Split this (e.g. imported) transaction using the attached receipt's items."""
        from ui.dialogs.receipt_dialog import ReceiptReviewDialog
        from parsers import receipt as R
        txn = self._open_target(txn)
        rc = self.db.get_receipt(rc["id"]) or rc
        if rc.get("status") == "booked" and rc.get("transaction_id") != txn.get("id") and \
                not txn.get("split_group_id"):
            rc = dict(rc, status="attached", transaction_id=txn.get("id"))
        parsed = None
        if not self.db.get_receipt_items(rc["id"]) and rc.get("file_path"):
            parsed = R.parse_file(self.db.receipt_abs_path(rc))
        ReceiptReviewDialog(self.db, rc, parent=self, parsed=parsed).exec()
        self._apply_filter(keep=txn)

    # Register-only keys list_register_rows adds; never saved.
    _ROW_KEYS = ("lines", "line_ids", "is_split", "line_count", "match_amount",
                 "matched_lines", "split_source")

    def _set_category(self, txn, value):
        """Quick re-file from the context menu. value is a category id, or
        "acct:<id>" to make the entry a transfer to/from that account (linking
        the other side if it was already imported). On a split it asks, then
        replaces the lines with that one account (Remove Split)."""
        from utils.chart_of_accounts import split_value
        if txn.get("is_split"):
            n = int(txn.get("line_count") or 0)
            if QMessageBox.question(
                    self, "Replace the Split?",
                    f"This transaction is split into {n} lines. File the whole "
                    f"${abs(float(txn.get('amount') or 0)):,.2f} under one account instead?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            row = self.db.remove_split(txn["split_group_id"], value)
            self._apply_filter(keep=row or txn)
            return
        kind, target = split_value(value)
        txn = {k: v for k, v in dict(txn).items() if k not in self._ROW_KEYS}
        if kind == "account":
            txn = self.db.save_transfer_pair(txn, target, keep_sign=True, match_existing=True)
        else:
            if str(txn.get("is_transfer", "0")) == "1" and not txn.get("split_group_id"):
                self.db.clear_transfer(txn)
            txn["category_id"] = target
            self.db.save_transaction(txn)
        self._apply_filter(keep=txn)

    def _remove_split(self, txn):
        """Remove Split: one account for the whole amount — asks which."""
        from ui.dialogs.transaction_dialog import ask_remove_split_target
        target = self._open_target(txn)
        value = ask_remove_split_target(self.db, target, self)
        if value is None:
            return
        row = self.db.remove_split(target["split_group_id"], value)
        self._apply_filter(keep=row or txn)

    def _unlink_transfer(self, txn):
        """Undo a transfer link (e.g. one the Import page's Match column made):
        both entries stay, as ordinary uncategorized transactions."""
        reply = QMessageBox.question(
            self, "Unlink Transfer",
            "Unlink this transfer? Both transactions are kept as ordinary entries "
            "(uncategorized), and will count in reports again once categorized.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.db.unlink_transfer(txn["id"])
        except Exception as e:
            QMessageBox.warning(self, "Unlink Failed", str(e))
            return
        self._apply_filter()

    def _delete_txn(self, txn):
        """Delete the whole transaction: every line of a split and the other
        side of a transfer (or of a split line moved to another account)."""
        txn = self._open_target(txn)
        is_split = bool(txn.get("is_split") or txn.get("split_group_id"))
        is_transfer = str(txn.get("is_transfer", "0")) == "1" and txn.get("transfer_pair_id")
        if is_split:
            n = int(txn.get("line_count")
                    or len(self.db.get_split_group(txn["split_group_id"])))
            extra = (f"\n\nThis transaction is split into {n} lines — the whole "
                     "transaction is deleted (and any part moved to another account).")
        elif is_transfer:
            extra = ("\n\nThis is a transfer — the matching entry in the other "
                     "account will be deleted too.")
        else:
            extra = ""
        reply = QMessageBox.question(self, "Delete Transaction",
            f"Delete transaction on {fmt_date(txn.get('date',''))} for "
            f"${abs(float(txn.get('amount',0) or 0)):,.2f}?{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_transaction_full(txn)
            self._apply_filter()
