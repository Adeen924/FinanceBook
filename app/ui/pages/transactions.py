from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QComboBox, QLineEdit, QMessageBox, QCheckBox,
                              QLabel, QHeaderView, QMenu)
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QAction
from ui.widgets import (PageTitle, DataTable, SecondaryButton, DangerButton,
                        DateField, fmt_date)
from ui.styles  import SUCCESS, DANGER, WARNING


class TransactionsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    # Fields the register can be sorted by (key → menu label). Balance and the
    # reconciled flag are intentionally not sortable.
    _SORT_FIELDS = [("date", "Date"), ("account", "Account"),
                    ("payee", "Payee / Memo"), ("category", "Category")]

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
        self._cat_filter.addItem("All Categories", "")
        filt.addWidget(self._cat_filter)

        filt.addWidget(QLabel("From:"))
        self._date_from = DateField()
        self._date_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        filt.addWidget(self._date_from)

        filt.addWidget(QLabel("To:"))
        self._date_to = DateField()
        self._date_to.setDate(QDate.currentDate())
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

        filter_btn = QPushButton("Filter")
        filter_btn.clicked.connect(self._apply_filter)
        filt.addWidget(filter_btn)

        clear_btn = SecondaryButton("Clear")
        clear_btn.clicked.connect(self._clear_filter)
        filt.addWidget(clear_btn)

        lay.addLayout(filt)

        # ── Summary bar ──
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("Muted")
        lay.addWidget(self._summary_label)

        # ── Table ──
        self._table = DataTable(
            ["Date", "Account", "Payee / Memo", "Category", "Amount", "Balance", "R", ""])
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
        lay.addWidget(self._table)

        self._txns: list[dict] = []

    def refresh(self):
        accounts = self.db.get_accounts()
        cats = self.db.get_categories()

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
        self._cat_filter.addItem("All Categories", "")
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

    def _apply_filter(self):
        acct_id = self._acct_filter.currentData() or None
        start = self._date_from.date().toString("yyyy-MM-dd")
        end   = self._date_to.date().toString("yyyy-MM-dd")
        search = self._search_edit.text().lower().strip()
        cat_id = self._cat_filter.currentData() or None
        show_transfers = self._show_transfers.isChecked()

        txns = self.db.get_transactions(account_id=acct_id, start=start, end=end,
                                         include_transfers=show_transfers)
        if cat_id:
            txns = [t for t in txns if t.get("category_id") == cat_id]
        if search:
            txns = [t for t in txns
                    if search in (t.get("payee","") or "").lower()
                    or search in (t.get("memo","") or "").lower()
                    or search in (t.get("notes","") or "").lower()]

        self._txns = txns
        self._sort_and_render()

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
        cats  = {c["id"]: c for c in self.db.get_categories()}
        accts = {a["id"]: a for a in self.db.get_accounts()}

        def acct_name(t):
            return accts.get(t.get("account_id", ""), {}).get("name", "").lower()

        def payee_name(t):
            return (t.get("payee") or t.get("memo") or "").lower()

        def cat_name(t):
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

    def _sort_and_render(self):
        self._sort_txns()
        self._render_table()
        # In the default register view (Date ascending) the newest row is at the
        # bottom, so open scrolled there — matches how QuickBooks shows a
        # register. Any other sort reads top-down, so start at the top.
        if self._sort_key == "date" and self._sort_asc:
            self._table.scrollToBottom()
        else:
            self._table.scrollToTop()

    def _clear_filter(self):
        self._acct_filter.setCurrentIndex(0)
        self._cat_filter.setCurrentIndex(0)
        self._date_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        self._date_to.setDate(QDate.currentDate())
        self._search_edit.clear()
        self._show_transfers.setChecked(True)
        self._apply_filter()

    def show_account(self, account_id: str):
        """Filter the page to one account, newest first — used when an account
        name is clicked on the Accounts or Dashboard page."""
        self._cat_filter.setCurrentIndex(0)
        self._search_edit.clear()
        self._show_transfers.setChecked(True)
        # Widen the date range so recent transactions show regardless of year.
        self._date_from.setDate(QDate(2000, 1, 1))
        self._date_to.setDate(QDate.currentDate())
        # Select the account in the filter dropdown.
        for i in range(self._acct_filter.count()):
            if self._acct_filter.itemData(i) == account_id:
                self._acct_filter.setCurrentIndex(i)
                break
        self._apply_filter()

    def _render_table(self):
        cats   = {c["id"]: c for c in self.db.get_categories()}
        accts  = {a["id"]: a for a in self.db.get_accounts()}

        self._table.clear_rows()
        self._table.setRowCount(len(self._txns))
        total = 0.0

        # Running balance only makes sense for a single account, so it's shown
        # when one account is selected in the filter (blank under "All
        # Accounts"). Balances are the true account balance after each txn,
        # computed over the account's full history — see running_balances().
        acct_id = self._acct_filter.currentData() or None
        balances = self.db.running_balances(acct_id) if acct_id else {}
        att_ids, att_groups = self.db.attachment_index()

        for row, t in enumerate(self._txns):
            cat = cats.get(t.get("category_id",""), {})
            parent = cats.get(cat.get("parent_id",""), {})
            cat_name = f"{parent['name']} → {cat['name']}" if parent else cat.get("name","")
            acct_name = accts.get(t.get("account_id",""), {}).get("name","—")
            payee = t.get("payee") or t.get("memo") or "—"
            amt = float(t.get("amount") or 0)
            total += amt

            if str(t.get("is_transfer","0")) == "1":
                disp_payee = f"[Transfer] {payee}"
            elif t.get("split_group_id"):
                disp_payee = f"⑂ {payee}"
            else:
                disp_payee = payee
            if t.get("id") in att_ids or (t.get("split_group_id") or "-") in att_groups:
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

        income  = sum(float(t.get("amount",0)) for t in self._txns if float(t.get("amount",0)) > 0)
        expense = sum(float(t.get("amount",0)) for t in self._txns if float(t.get("amount",0)) < 0)
        self._summary_label.setText(
            f"{len(self._txns)} transactions   |   "
            f"Income: ${income:,.2f}   |   "
            f"Expenses: (${abs(expense):,.2f})   |   "
            f"Net: ${total:,.2f}"
        )

    def _add(self):
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, parent=self)
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            # A split or transfer is already persisted by the dialog.
            if not getattr(dlg, "handled", False):
                self.db.save_transaction(dlg.get_data())
            self._apply_filter()

    def _edit_selected(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._txns):
            return
        self._edit_txn(self._txns[row])

    def _edit_txn(self, txn):
        from ui.dialogs.transaction_dialog import TransactionDialog
        dlg = TransactionDialog(self.db, transaction=txn, parent=self)
        if dlg.exec() == TransactionDialog.DialogCode.Accepted:
            # A split or transfer is already persisted by the dialog — don't
            # re-save the (now-replaced) original on top of it.
            if not getattr(dlg, "handled", False):
                self.db.save_transaction(dlg.get_data())
            self._apply_filter()

    def _split_txn(self, txn):
        if str(txn.get("is_transfer","0")) == "1":
            QMessageBox.information(self, "Cannot Split",
                "Transfers can't be split. Remove the transfer link first.")
            return
        if txn.get("loan_id"):
            QMessageBox.information(self, "Cannot Split",
                "Loan payments can't be split — they already track principal "
                "and interest separately.")
            return
        from ui.dialogs.split_dialog import SplitDialog
        dlg = SplitDialog(self.db, txn, parent=self)
        if dlg.exec() == SplitDialog.DialogCode.Accepted:
            self.db.split_transaction(txn, dlg.get_splits())
            self._apply_filter()

    def _context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._txns):
            return
        txn = self._txns[row]
        menu = QMenu(self)

        edit_act = QAction("Edit", self)
        edit_act.triggered.connect(lambda: self._edit_txn(txn))
        menu.addAction(edit_act)

        split_label = "Edit Split…" if txn.get("split_group_id") else "Split…"
        split_act = QAction(split_label, self)
        split_act.triggered.connect(lambda: self._split_txn(txn))
        menu.addAction(split_act)

        # Quick category submenu
        cat_menu = menu.addMenu("Set Category")
        cats = self.db.get_categories()
        roots = [c for c in cats if not c.get("parent_id")]
        for cat in roots:
            act = QAction(cat["name"], self)
            act.triggered.connect(lambda _, c=cat: self._set_category(txn, c["id"]))
            cat_menu.addAction(act)
            subs = [s for s in cats if s.get("parent_id") == cat["id"]]
            if subs:
                for sub in subs:
                    sub_act = QAction(f"    ↳ {sub['name']}", self)
                    sub_act.triggered.connect(lambda _, s=sub: self._set_category(txn, s["id"]))
                    cat_menu.addAction(sub_act)

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
        self.db.attach_receipt(rc["id"], txn)
        self._apply_filter()

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
        rc = self.db.get_receipt(rc["id"]) or rc
        if rc.get("status") == "booked" and rc.get("transaction_id") != txn.get("id") and \
                not txn.get("split_group_id"):
            rc = dict(rc, status="attached", transaction_id=txn.get("id"))
        parsed = None
        if not self.db.get_receipt_items(rc["id"]) and rc.get("file_path"):
            parsed = R.parse_file(self.db.receipt_abs_path(rc))
        ReceiptReviewDialog(self.db, rc, parent=self, parsed=parsed).exec()
        self._apply_filter()

    def _set_category(self, txn, cat_id):
        txn["category_id"] = cat_id
        self.db.save_transaction(txn)
        self._apply_filter()

    def _delete_txn(self, txn):
        gid = txn.get("split_group_id")
        if gid:
            members = self.db.get_split_group(gid)
            if len(members) > 1:
                box = QMessageBox(self)
                box.setWindowTitle("Delete Split Transaction")
                box.setText(
                    f"This is one line of a split with {len(members)} parts.\n\n"
                    "Delete the entire split, or just this one line?")
                whole_btn = box.addButton("Delete Entire Split",
                                          QMessageBox.ButtonRole.DestructiveRole)
                line_btn  = box.addButton("Delete This Line Only",
                                          QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                clicked = box.clickedButton()
                if clicked == whole_btn:
                    self.db.delete_split_group(gid)
                    # If the split was also a transfer, remove the mirror too.
                    self.db.delete_transfer_pair(txn.get("transfer_pair_id", ""))
                    self._apply_filter()
                elif clicked == line_btn:
                    self.db.delete_transaction(txn["id"])
                    self._apply_filter()
                return

        is_transfer = str(txn.get("is_transfer", "0")) == "1" and txn.get("transfer_pair_id")
        extra = ("\n\nThis is a transfer — the matching entry in the other "
                 "account will be deleted too.") if is_transfer else ""
        reply = QMessageBox.question(self, "Delete Transaction",
            f"Delete transaction on {txn.get('date','')} for "
            f"${abs(float(txn.get('amount',0))):,.2f}?{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_transaction_full(txn)
            self._apply_filter()
