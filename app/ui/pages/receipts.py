"""Receipts — add files, review line items, save them to the books, search items."""
import os

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QFileDialog, QMessageBox, QHeaderView, QTabWidget, QLineEdit,
                             QFrame, QProgressDialog)
from PyQt6.QtCore import Qt, QDate, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices

from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, fmt_date
from ui.styles import WARNING, SUCCESS, MUTED, PRIMARY
from utils.money import money_sum, fmt
from parsers import receipt as R

ROW_H = 44
_STATUS = {"new": ("New", WARNING), "reviewed": ("Draft", MUTED),
           "attached": ("Attached", PRIMARY), "booked": ("Saved", SUCCESS)}
FILE_FILTER = ("Receipts (*.jpg *.jpeg *.png *.gif *.webp *.pdf *.txt *.eml *.html *.htm);;"
               "All files (*.*)")


class DropZone(QFrame):
    """Dashed drop target; calls on_files(list_of_paths)."""
    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self._on_files = on_files
        self.setAcceptDrops(True)
        self.setObjectName("DropZone")
        self.setMinimumHeight(84)
        self._idle_css = ("QFrame#DropZone { border: 2px dashed rgba(47,107,255,0.35); "
                          "border-radius: 16px; background: rgba(255,255,255,0.45); }")
        self.setStyleSheet(self._idle_css)
        lay = QVBoxLayout(self)
        lbl = QLabel("Drop receipt files here — photos, PDFs, emails (.eml), web pages or "
                     "text. FinanceBook keeps its own copy in a <b>FinanceBook Receipts</b> "
                     "folder next to your database; your originals aren't moved.")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setObjectName("Muted")
        lay.addWidget(lbl)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(self._idle_css.replace("0.35", "0.9").replace("0.45", "0.75"))

    def dragLeaveEvent(self, e):
        self.setStyleSheet(self._idle_css)

    def dropEvent(self, e):
        self.setStyleSheet(self._idle_css)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._on_files(paths)


class ParseWorker(QThread):
    """Reads receipt files (PDF text, Windows OCR) off the UI thread."""
    done = pyqtSignal(list)          # [(receipt_id, parsed result)]

    def __init__(self, jobs):
        super().__init__()
        self._jobs = list(jobs)      # [(receipt_id, path)]

    def run(self):
        out = []
        for rid, path in self._jobs:
            try:
                res = R.parse_file(path)
            except Exception:        # a bad file must never crash the app
                res = R.empty_result("manual")
                res["warnings"] = [R.MSG_BAD_PDF if path.lower().endswith(".pdf")
                                   else R.MSG_IMAGE]
            out.append((rid, res))
        self.done.emit(out)


def receipt_fields(parsed: dict) -> dict:
    """The receipts-table columns filled from a parse result."""
    out = {"vendor": parsed["vendor"], "receipt_date": parsed["date"],
           "parse_method": parsed["method"], "raw_text": parsed["raw_text"]}
    for k in R.MONEY_FIELDS:
        out[k] = parsed.get(k) or 0
    if parsed.get("order_number"):
        out["notes"] = f"Order # {parsed['order_number']}"
    return out


class ReceiptsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._receipts: list[dict] = []
        self._workers: set = set()
        self.last_worker = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Receipts"))
        hdr.addStretch()
        manual_btn = SecondaryButton("Enter Manually")
        manual_btn.clicked.connect(self._manual)
        hdr.addWidget(manual_btn)
        self._add_btn = QPushButton("+ Add Receipts…")
        self._add_btn.clicked.connect(self._browse)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        lay.addWidget(DropZone(self.add_files))

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)

        # ── receipts list
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        srow = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search vendor, file name or item…")
        self._search.returnPressed.connect(self.refresh)
        srow.addWidget(self._search, 1)
        go = SecondaryButton("Search")
        go.clicked.connect(self.refresh)
        srow.addWidget(go)
        pl.addLayout(srow)
        self._table = DataTable(["Date", "Vendor", "File", "Total", "Status", "Actions"])
        hh = self._table.horizontalHeader()
        self._table.setColumnWidth(0, 100)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(3, 110)
        self._table.setColumnWidth(4, 90)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, 230)
        hh.setStretchLastSection(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.doubleClicked.connect(lambda idx: self._review(self._receipts[idx.row()]))
        pl.addWidget(self._table)
        self._count = QLabel("")
        self._count.setObjectName("Muted")
        pl.addWidget(self._count)
        self._tabs.addTab(page, "Receipts")

        # ── item search
        page2 = QWidget()
        l2 = QVBoxLayout(page2)
        l2.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self._item_q = QLineEdit()
        self._item_q.setPlaceholderText("What did I spend on…  (e.g. printer paper)")
        self._item_q.returnPressed.connect(self._search_items)
        row.addWidget(self._item_q, 1)
        row.addWidget(QLabel("From:"))
        self._i_from = DateField()
        self._i_from.setDate(QDate(QDate.currentDate().year(), 1, 1))
        row.addWidget(self._i_from)
        row.addWidget(QLabel("To:"))
        self._i_to = DateField()
        row.addWidget(self._i_to)
        b = QPushButton("Find")
        b.clicked.connect(self._search_items)
        row.addWidget(b)
        l2.addLayout(row)
        self._items = DataTable(["Date", "Vendor", "Item", "Qty", "Unit", "Total", "Category"])
        ih = self._items.horizontalHeader()
        self._items.setColumnWidth(0, 100)
        self._items.setColumnWidth(1, 170)
        ih.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c, w in ((3, 60), (4, 90), (5, 100), (6, 200)):
            self._items.setColumnWidth(c, w)
        ih.setStretchLastSection(False)
        l2.addWidget(self._items)
        self._items_sum = QLabel("")
        self._items_sum.setObjectName("Muted")
        l2.addWidget(self._items_sum)
        self._tabs.addTab(page2, "Find Items")

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self):
        self._receipts = self.db.get_receipts(self._search.text().strip())
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._receipts))
        for r, rc in enumerate(self._receipts):
            t.set_item(r, 0, fmt_date(rc.get("receipt_date") or rc.get("uploaded_at", "")[:10]))
            t.set_item(r, 1, rc.get("vendor") or "—", bold=bool(rc.get("vendor")))
            t.set_item(r, 2, rc.get("original_name") or "(manual)")
            t.money_item(r, 3, -float(rc.get("total") or 0) if rc.get("total") else 0.0)
            label, color = _STATUS.get(rc.get("status", "new"), ("New", WARNING))
            t.set_item(r, 4, label, color=color, bold=True)
            t.setCellWidget(r, 5, self._actions(rc))
            t.setRowHeight(r, ROW_H)
        n = len(self._receipts)
        pending = sum(1 for x in self._receipts if x.get("status") != "booked")
        self._count.setText(f"{n} receipt{'s' if n != 1 else ''}  ·  {pending} not yet saved "
                            "to the books  ·  double-click to review")

    def _actions(self, rc) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(6)
        rev = QPushButton("Review")
        rev.setObjectName("CellButton")
        rev.setMinimumWidth(72)
        rev.clicked.connect(lambda _, x=rc: self._review(x))
        lay.addWidget(rev)
        op = QPushButton("Open")
        op.setObjectName("CellButton")
        op.setMinimumWidth(60)
        path = self.db.receipt_abs_path(rc)
        op.setEnabled(bool(path and os.path.exists(path)))
        op.clicked.connect(lambda _, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
        lay.addWidget(op)
        de = QPushButton("Delete")
        de.setObjectName("CellDanger")
        de.setMinimumWidth(66)
        de.clicked.connect(lambda _, x=rc: self._delete(x))
        lay.addWidget(de)
        return cell

    # ── actions ──────────────────────────────────────────────────────────────

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Receipts", os.path.expanduser("~"),
                                                FILE_FILTER)
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]):
        dups, bad, jobs = [], [], []
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if not os.path.isfile(p) or ext not in R.SUPPORTED_EXTS:
                bad.append(os.path.basename(p))
                continue
            try:
                rc, dup = self.db.add_receipt_file(p)
            except OSError as e:
                bad.append(f"{os.path.basename(p)} ({e.strerror})")
                continue
            if dup:
                dups.append(rc)
                continue
            jobs.append((rc["id"], self.db.receipt_abs_path(rc)))
        self.refresh()
        notes = []
        if dups:
            notes.append("Already on file (not added again): " +
                         ", ".join(d.get("original_name") or d["id"] for d in dups))
        if bad:
            notes.append("Skipped (unsupported or unreadable): " + ", ".join(bad))
        if notes:
            QMessageBox.information(self, "Receipts", "\n\n".join(notes))
        if jobs:
            self._parse_async(jobs, self._parsed_new)

    def _parse_async(self, jobs, then):
        """Parse files on a worker thread behind a progress dialog, then call then(results)."""
        prog = QProgressDialog("Reading receipt…" if len(jobs) == 1
                               else f"Reading {len(jobs)} receipts…", "", 0, 0, self)
        prog.setCancelButton(None)
        prog.setWindowTitle("Receipts")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(300)
        w = ParseWorker(jobs)
        self._workers.add(w)
        self.last_worker = w

        def finished(results):
            prog.close()
            then(results)
        w.done.connect(finished)
        w.finished.connect(lambda w=w: self._workers.discard(w))
        w.start()

    def _parsed_new(self, results):
        added = []
        for rid, parsed in results:
            self.db.save_receipt(dict(receipt_fields(parsed), id=rid))
            rc = self.db.get_receipt(rid)
            if rc:
                added.append((rc, parsed))
        self.refresh()
        if len(added) == 1:
            self._review(*added[0])

    def _manual(self):
        rc = self.db.add_manual_receipt()
        self.refresh()
        self._review(rc)

    def _review(self, rc, parsed=None):
        from ui.dialogs.receipt_dialog import ReceiptReviewDialog
        rc = self.db.get_receipt(rc["id"]) or rc
        if parsed is None and not self.db.get_receipt_items(rc["id"]) \
                and rc.get("status") != "booked" and rc.get("file_path"):
            self._parse_async([(rc["id"], self.db.receipt_abs_path(rc))],
                              lambda res, rc=rc: self._review(rc, res[0][1]))
            return
        dlg = ReceiptReviewDialog(self.db, rc, parent=self, parsed=parsed)
        dlg.exec()
        self.refresh()

    def _delete(self, rc):
        booked = rc.get("status") == "booked"
        msg = (f"Delete the receipt “{rc.get('vendor') or rc.get('original_name') or 'manual'}”"
               " and its copied file?")
        if booked:
            msg += "\n\nThe transaction it created stays in your books."
        if QMessageBox.question(self, "Delete Receipt", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db.delete_receipt(rc["id"])
            self.refresh()

    def _search_items(self):
        q = self._item_q.text().strip()
        if not q:
            return
        rows = self.db.search_receipt_items(q, self._i_from.date().toString("yyyy-MM-dd"),
                                            self._i_to.date().toString("yyyy-MM-dd"))
        cats = {c["id"]: c["name"] for c in self.db.get_categories()}
        t = self._items
        t.clear_rows()
        t.setRowCount(len(rows))
        for r, it in enumerate(rows):
            t.set_item(r, 0, fmt_date(it.get("receipt_date", "")))
            t.set_item(r, 1, it.get("vendor") or "—")
            t.set_item(r, 2, it.get("description", ""))
            t.set_item(r, 3, f"{it.get('qty', 1):g}",
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 4, fmt(it.get("unit_price")),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 5, fmt(it.get("line_total")),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, bold=True)
            t.set_item(r, 6, cats.get(it.get("category_id", ""), "—"))
        total = money_sum(i.get("line_total") for i in rows)
        self._items_sum.setText(f"{len(rows)} matching item{'s' if len(rows) != 1 else ''}  ·  "
                                f"Total spent: {fmt(total)}")
