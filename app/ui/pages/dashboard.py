from datetime import date, timedelta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QFrame, QScrollArea, QGridLayout, QPushButton)
from PyQt6.QtCore import Qt
from ui.widgets import PageTitle, MutedLabel, DataTable, Card
from ui.styles import SUCCESS, DANGER, WARNING, ACCENT


class AccountCard(QFrame):
    def __init__(self, name, acct_type, institution, balance,
                 account_id="", on_click=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(200)
        self._account_id = account_id
        self._on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Click to view this account's transactions")
        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        type_lbl = QLabel(acct_type.title())
        type_lbl.setObjectName("Muted")
        lay.addWidget(type_lbl)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight:bold; font-size:14px;")
        name_lbl.setWordWrap(True)
        lay.addWidget(name_lbl)

        if institution:
            inst_lbl = QLabel(institution)
            inst_lbl.setObjectName("Muted")
            lay.addWidget(inst_lbl)

        color = SUCCESS if balance >= 0 else DANGER
        bal_lbl = QLabel(f"${balance:,.2f}" if balance >= 0 else f"(${abs(balance):,.2f})")
        bal_lbl.setStyleSheet(f"font-size:18px; font-weight:bold; color:{color}; margin-top:6px;")
        lay.addWidget(bal_lbl)

    def mousePressEvent(self, event):
        if self._on_click is not None and self._account_id:
            self._on_click(self._account_id)
        super().mousePressEvent(event)


class DashboardPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(28, 24, 28, 24)
        main.setSpacing(20)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Dashboard"))
        hdr.addStretch()
        self._alert_label = QLabel("")
        self._alert_label.setWordWrap(True)
        hdr.addWidget(self._alert_label)
        main.addLayout(hdr)

        # Account cards row (scrollable)
        self._cards_area = QScrollArea()
        self._cards_area.setWidgetResizable(True)
        self._cards_area.setFixedHeight(145)
        self._cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_widget = QWidget()
        self._cards_layout = QHBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(12)
        self._cards_layout.addStretch()
        self._cards_area.setWidget(self._cards_widget)
        main.addWidget(self._cards_area)

        # Totals row
        self._totals_row = QHBoxLayout()
        main.addLayout(self._totals_row)

        # Recent transactions
        recent_lbl = QLabel("Recent Transactions (30 days, excluding transfers)")
        recent_lbl.setStyleSheet("font-weight:bold; font-size:14px;")
        main.addWidget(recent_lbl)

        self._table = DataTable(["Date", "Account", "Payee / Memo", "Category", "Amount"])
        self._table.horizontalHeader().setSectionResizeMode(2, self._table.horizontalHeader().ResizeMode.Stretch)
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(1, 150)
        self._table.setColumnWidth(4, 110)
        main.addWidget(self._table)

    def refresh(self):
        self._load_cards()
        self._load_recent()
        self._load_alerts()

    def _open_account(self, account_id: str):
        mw = getattr(self, "_main_window", None)
        if mw is not None:
            mw.open_account_transactions(account_id)

    def _load_cards(self):
        # Clear existing cards (keep stretch at end)
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        accounts = self.db.get_accounts()
        total = 0.0
        for acct in accounts:
            bal = self.db.account_balance(acct["id"])
            total += bal
            card = AccountCard(acct["name"], acct.get("type",""),
                               acct.get("institution",""), bal,
                               account_id=acct["id"], on_click=self._open_account)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

        if not accounts:
            placeholder = QLabel("No accounts yet — go to Accounts to add one.")
            placeholder.setObjectName("Muted")
            self._cards_layout.insertWidget(0, placeholder)

        # Totals
        while self._totals_row.count():
            item = self._totals_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        color = SUCCESS if total >= 0 else DANGER
        total_lbl = QLabel(f"Total Net Worth:  <span style='color:{color}; font-weight:bold;'>"
                           f"{'${:,.2f}'.format(total) if total >= 0 else '(${:,.2f})'.format(abs(total))}</span>")
        total_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._totals_row.addWidget(total_lbl)
        self._totals_row.addStretch()

    def _load_recent(self):
        start = (date.today() - timedelta(days=30)).isoformat()
        txns = self.db.get_transactions(start=start, include_transfers=False)[:25]
        cats = {c["id"]: c for c in self.db.get_categories()}
        accts = {a["id"]: a for a in self.db.get_accounts()}

        self._table.clear_rows()
        self._table.setRowCount(len(txns))
        for row, t in enumerate(txns):
            cat = cats.get(t.get("category_id",""), {})
            parent = cats.get(cat.get("parent_id",""), {})
            cat_name = f"{parent['name']} → {cat['name']}" if parent else cat.get("name","")
            acct_name = accts.get(t.get("account_id",""), {}).get("name","—")
            payee = t.get("payee") or t.get("memo") or "—"

            self._table.set_item(row, 0, t.get("date",""))
            self._table.set_item(row, 1, acct_name)
            self._table.set_item(row, 2, payee)
            self._table.set_item(row, 3, cat_name or "⚠ Uncategorized")
            self._table.money_item(row, 4, float(t.get("amount") or 0))

    def _load_alerts(self):
        all_txns = self.db.get_transactions(include_transfers=False)
        uncategorized = sum(1 for t in all_txns if not t.get("category_id"))
        alerts = []
        if uncategorized:
            alerts.append(f"⚠ {uncategorized} uncategorized transactions")
        self._alert_label.setText("   ".join(alerts))
        self._alert_label.setStyleSheet(f"color:{WARNING}; font-weight:bold;" if alerts else "")
