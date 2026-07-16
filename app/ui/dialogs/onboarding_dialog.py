"""
First-run welcome guide.

A short, paged walkthrough shown ONCE — the very first time someone opens
FinanceBook on their computer. It is not shown again after app updates (see
main.py, which only launches it on first run and records a persistent flag).

The steps mirror the app's pages so a new user knows where everything lives and
how to get their data in.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QStackedWidget, QWidget, QFrame)
from PyQt6.QtCore import Qt


# Each step: (emoji, title, HTML body)
STEPS = [
    ("👋", "Welcome to FinanceBook",
     "FinanceBook is a personal & small-business finance tracker that runs "
     "entirely on <b>your</b> computer — your data lives in a single database "
     "file you control, and nothing is sent to a server.<br><br>"
     "This quick guide (about a minute) shows you where everything is and how "
     "to get started. You can skip it anytime."),

    ("🧭", "Finding your way around",
     "Everything is reached from the <b>sidebar</b> on the left:<br><br>"
     "• <b>Dashboard</b> — account balances, net worth, and recent activity<br>"
     "• <b>Accounts</b> — your bank, credit-card, loan and cash accounts<br>"
     "• <b>Transactions</b> — every entry, with search and filters<br>"
     "• <b>Import</b> — bring in bank/QuickBooks files<br>"
     "• <b>Categories, Rules, Transfers, Reconcile, Reports</b> — organize and review<br>"
     "• <b>Settings</b> (bottom) — database name, migration wizard, and more"),

    ("🏦", "Step 1 — Add your accounts",
     "Go to the <b>Accounts</b> page and click <b>+ Add Account</b>. Give each "
     "one a name (e.g. “Chase Checking”), pick a type, and set its starting "
     "balance.<br><br>"
     "Tip: on the Accounts page and the Dashboard, <b>click an account's name</b> "
     "to jump straight to that account's transactions. Accounts you no longer "
     "use can be marked <i>Inactive</i> (when their balance is $0)."),

    ("🏷️", "Step 2 — Categories",
     "FinanceBook comes with a full set of income & expense <b>categories</b> "
     "ready to go (Groceries, Rent, Salary, and many more).<br><br>"
     "Visit the <b>Categories</b> page to add your own or adjust them. When you "
     "enter an amount on a transaction, the category decides the direction — an "
     "expense subtracts, income adds."),

    ("📥", "Step 3 — Import your transactions",
     "Two ways to bring data in:<br><br>"
     "<b>1. Bank files</b> — on the <b>Import</b> page, pick the account, choose "
     "your <b>OFX / QFX / CSV / Excel</b> file, click <i>Parse</i>, review, then "
     "<i>Import All</i>.<br><br>"
     "<b>2. Moving from QuickBooks</b> — open <b>Settings → Open Import "
     "Wizard</b>:<br>"
     "&nbsp;&nbsp;• <b>Step 1</b> imports your accounts &amp; categories (IIF file)<br>"
     "&nbsp;&nbsp;• <b>Step 2</b> imports your transactions (Excel “Transaction "
     "Detail by Account”)"),

    ("📋", "Step 4 — Working with transactions",
     "On the <b>Transactions</b> page click <b>+ Add Transaction</b>. From that "
     "one screen you can also:<br><br>"
     "• <b>Split</b> a purchase across multiple categories (e.g. a Costco run)<br>"
     "• Mark it a <b>Transfer</b> between two of your accounts (kept out of P&amp;L)<br>"
     "• Record a <b>loan payment</b> with principal/interest<br><br>"
     "Type in the category box to filter instantly instead of scrolling."),

    ("✅", "Step 5 — Rules, Reconcile & Reports",
     "• <b>Rules</b> auto-categorize transactions by payee so you don't have to.<br>"
     "• <b>Reconcile</b> checks your entries against a bank statement.<br>"
     "• <b>Reports</b> gives you Profit &amp; Loss and a Balance Sheet you can "
     "export.<br><br>"
     "The <b>Dashboard</b> ties it together with balances and net worth."),

    ("💾", "A note on your data",
     "Your database is just a file. To keep it backed up and synced across "
     "computers, save it in a <b>Google Drive, OneDrive, or network</b> folder "
     "(you chose the location when you named it).<br><br>"
     "You can create or switch databases anytime from the dropdown at the top "
     "of the sidebar. Updates to the app never touch your data."),

    ("🎉", "You're all set!",
     "That's the tour. A good first move is to <b>add an account</b> or run the "
     "<b>Import Wizard</b> in Settings.<br><br>"
     "You can reopen this guide anytime from <b>Settings → Show Welcome "
     "Guide</b>. Enjoy FinanceBook!"),
]


class OnboardingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to FinanceBook")
        self.setModal(True)
        self.setMinimumSize(660, 560)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Progress label
        top = QHBoxLayout()
        top.setContentsMargins(24, 18, 24, 0)
        self._progress = QLabel("")
        self._progress.setObjectName("Muted")
        top.addWidget(self._progress)
        top.addStretch()
        lay.addLayout(top)

        # Stacked steps
        self._stack = QStackedWidget()
        for emoji, title, body in STEPS:
            self._stack.addWidget(self._make_step(emoji, title, body))
        lay.addWidget(self._stack, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 0, 24, 20)
        self._skip_btn = QPushButton("Skip guide")
        self._skip_btn.setObjectName("Secondary")
        self._skip_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch()

        self._back_btn = QPushButton("Back")
        self._back_btn.setObjectName("Secondary")
        self._back_btn.clicked.connect(self._go_back)
        btn_row.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("Success")
        self._next_btn.clicked.connect(self._go_next)
        btn_row.addWidget(self._next_btn)
        lay.addLayout(btn_row)

        self._update_nav()

    def _make_step(self, emoji: str, title: str, body: str) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(28, 12, 28, 12)
        pl.setSpacing(14)

        head = QLabel(f"<span style='font-size:34px;'>{emoji}</span>")
        head.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pl.addWidget(head)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title_lbl.setStyleSheet("font-size:19px; font-weight:bold;")
        pl.addWidget(title_lbl)

        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(
            "QFrame#Card { background:#f8fafc; border:1px solid #e5e7eb; "
            "border-radius:8px; }")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        body_lbl = QLabel(body)
        body_lbl.setTextFormat(Qt.TextFormat.RichText)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet("font-size:13px; line-height:150%;")
        body_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        cl.addWidget(body_lbl)
        cl.addStretch()
        pl.addWidget(card, 1)

        return page

    def _go_next(self):
        i = self._stack.currentIndex()
        if i >= self._stack.count() - 1:
            self.accept()
            return
        self._stack.setCurrentIndex(i + 1)
        self._update_nav()

    def _go_back(self):
        i = self._stack.currentIndex()
        if i > 0:
            self._stack.setCurrentIndex(i - 1)
            self._update_nav()

    def _update_nav(self):
        i = self._stack.currentIndex()
        n = self._stack.count()
        self._progress.setText(f"Step {i + 1} of {n}")
        self._back_btn.setEnabled(i > 0)
        is_last = i == n - 1
        self._next_btn.setText("Finish" if is_last else "Next")
        self._skip_btn.setVisible(not is_last)
