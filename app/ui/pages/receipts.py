"""Receipts — add files, watch a receipts folder, review line items, save them to
the books, search items."""
import os
import threading

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QFileDialog, QMessageBox, QHeaderView, QTabWidget, QLineEdit,
                             QFrame, QProgressDialog, QCheckBox, QComboBox, QApplication)
from PyQt6.QtCore import Qt, QDate, QUrl, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QDesktopServices

import db_config
from ui.widgets import PageTitle, DataTable, SecondaryButton, DateField, fmt_date
from ui.styles import WARNING, SUCCESS, MUTED, PRIMARY
from utils.money import money_sum, fmt
from utils import receipt_inbox as INBOX
from utils.receipt_inbox import receipt_fields  # noqa: F401  (kept importable from here)
from parsers import receipt as R

ROW_H = 44
_STATUS = {"new": ("New", WARNING), "reviewed": ("Draft", MUTED),
           "attached": ("Attached", PRIMARY), "booked": ("Saved", SUCCESS)}
FILE_FILTER = ("Receipts (*.jpg *.jpeg *.png *.gif *.webp *.pdf *.txt *.eml *.html *.htm);;"
               "All files (*.*)")
FILTERS = [("all", "All receipts"), (INBOX.NEEDS_REVIEW, "Needs review"),
           (INBOX.NEEDS_PARSING, "Needs parsing"), (INBOX.SAVED, "Saved")]
READ_ONLY_TIP = ("This database is open read-only because it's in use on another computer, "
                 "so receipts can't be imported, parsed or saved here.")
RESCAN_DEBOUNCE_MS = 400
# Worker threads live here until they finish, not on the page: the page can be
# destroyed mid-scan (database switch), and a QThread garbage-collected while
# running aborts the whole app.
_RUNNING: set = set()


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
        self._lbl = QLabel("Drop receipt files here — photos, PDFs, emails (.eml), web pages "
                           "or text. FinanceBook keeps its own copy in a <b>FinanceBook "
                           "Receipts</b> folder next to your database; your originals aren't "
                           "moved.")
        self._lbl.setWordWrap(True)
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setObjectName("Muted")
        lay.addWidget(self._lbl)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.acceptDrops():
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


class ScanWorker(QThread):
    """Lists and classifies the watched folder off the UI thread."""
    done = pyqtSignal(object, int)   # (ScanResult, token)

    def __init__(self, folder, subfolders, receipts, copy_size, items_ids, token):
        super().__init__()
        self._args = (folder, receipts, copy_size, items_ids, subfolders)
        self.token = token
        self.cancelled = threading.Event()

    def run(self):
        folder, receipts, copy_size, items_ids, subfolders = self._args
        try:
            res = INBOX.scan_folder(folder, receipts, copy_size, items_ids, subfolders,
                                    should_cancel=self.cancelled.is_set)
        except Exception as e:       # never let a scan crash the app
            res = INBOX.ScanResult(folder=folder, error=f"The folder couldn't be scanned "
                                                        f"({type(e).__name__}).")
        self.done.emit(res, self.token)


class BatchWorker(QThread):
    """Imports + parses (or AI-reads) many receipts; stops after the current file on cancel."""
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(object)        # BatchSummary

    def __init__(self, fn, **kwargs):
        super().__init__()
        self._fn, self._kwargs = fn, kwargs
        self.cancelled = threading.Event()

    def run(self):
        try:
            res = self._fn(progress=lambda d, t, n: self.progress.emit(d, t, n),
                           should_cancel=self.cancelled.is_set, **self._kwargs)
        except Exception as e:       # pragma: no cover — import_and_parse catches per file
            res = INBOX.BatchSummary(failed=[("batch", type(e).__name__)])
        self.done.emit(res)


class ReceiptsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._receipts: list[dict] = []
        self.last_worker = None
        self.scan: INBOX.ScanResult | None = None
        self._scan_token = 0
        self._scan_worker = None
        self._dismissed = None           # scan signature when "Not now" was pressed
        self._parsed_cache: dict = {}    # receipt id → parse result from a batch
        self._filter_state = "all"
        self._build()
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(RESCAN_DEBOUNCE_MS)
        self._rescan_timer.timeout.connect(self.rescan)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.addWidget(PageTitle("Receipts"))
        hdr.addStretch()
        self._manual_btn = SecondaryButton("Enter Manually")
        self._manual_btn.clicked.connect(self._manual)
        hdr.addWidget(self._manual_btn)
        self._add_btn = QPushButton("+ Add Receipts…")
        self._add_btn.clicked.connect(self._browse)
        hdr.addWidget(self._add_btn)
        lay.addLayout(hdr)

        lay.addLayout(self._build_folder_row())
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("Muted")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._status_lbl.linkActivated.connect(self._on_status_link)
        lay.addWidget(self._status_lbl)
        lay.addWidget(self._build_banner())

        self._drop = DropZone(self.add_files)
        lay.addWidget(self._drop)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs, 1)

        # ── receipts list
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 8, 0, 0)
        srow = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search vendor, file name or item…")
        self._search.returnPressed.connect(self.refresh_table)
        srow.addWidget(self._search, 1)
        self._filter = QComboBox()
        for key, label in FILTERS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(self._on_filter)
        srow.addWidget(self._filter)
        go = SecondaryButton("Search")
        go.clicked.connect(self.refresh_table)
        srow.addWidget(go)
        self._queue_btn = SecondaryButton("Review Queue")
        self._queue_btn.setToolTip("Step through receipts that still need review, oldest first")
        self._queue_btn.clicked.connect(self.start_review_queue)
        srow.addWidget(self._queue_btn)
        pl.addLayout(srow)
        self._table = DataTable(["Date", "Vendor", "File", "Total", "Status", "Actions"])
        hh = self._table.horizontalHeader()
        self._table.setColumnWidth(0, 100)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(3, 110)
        self._table.setColumnWidth(4, 110)
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
        self._items = DataTable(["Date", "Vendor", "Item", "Qty", "Units", "Unit $", "Total",
                                 "Category"])
        ih = self._items.horizontalHeader()
        self._items.setColumnWidth(0, 100)
        self._items.setColumnWidth(1, 170)
        ih.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c, w in ((3, 60), (4, 110), (5, 90), (6, 100), (7, 200)):
            self._items.setColumnWidth(c, w)
        ih.setStretchLastSection(False)
        l2.addWidget(self._items)
        self._items_sum = QLabel("")
        self._items_sum.setObjectName("Muted")
        l2.addWidget(self._items_sum)
        self._tabs.addTab(page2, "Find Items")

    def _build_folder_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._folder_lbl = QLabel("")
        self._folder_lbl.setWordWrap(True)
        self._folder_lbl.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self._folder_lbl, 1)
        self._subfolders_chk = QCheckBox("Include subfolders")
        self._subfolders_chk.toggled.connect(self._on_subfolders)
        row.addWidget(self._subfolders_chk)
        self._choose_btn = SecondaryButton("Choose…")
        self._choose_btn.clicked.connect(self.choose_folder)
        row.addWidget(self._choose_btn)
        self._rescan_btn = SecondaryButton("Rescan")
        self._rescan_btn.clicked.connect(self.rescan)
        row.addWidget(self._rescan_btn)
        self._clear_btn = SecondaryButton("Clear")
        self._clear_btn.setToolTip("Stop watching this folder (its files aren't touched)")
        self._clear_btn.clicked.connect(self.clear_folder)
        row.addWidget(self._clear_btn)
        return row

    def _build_banner(self) -> QFrame:
        self._banner = QFrame()
        self._banner.setObjectName("InboxBanner")
        row = QHBoxLayout(self._banner)
        row.setContentsMargins(14, 8, 10, 8)
        self._banner_lbl = QLabel("")
        self._banner_lbl.setWordWrap(True)
        row.addWidget(self._banner_lbl, 1)
        self._parse_btn = QPushButton("Parse")
        self._parse_btn.clicked.connect(self.parse_inbox)
        row.addWidget(self._parse_btn)
        self._banner_review_btn = SecondaryButton("Review")
        self._banner_review_btn.clicked.connect(self.start_review_queue)
        row.addWidget(self._banner_review_btn)
        self._notnow_btn = SecondaryButton("Not now")
        self._notnow_btn.clicked.connect(self._not_now)
        row.addWidget(self._notnow_btn)
        self._banner.hide()
        return self._banner

    # ── data ─────────────────────────────────────────────────────────────────

    def refresh(self):
        """Called when the page is shown: table now, folder rescan shortly after."""
        self._apply_read_only()
        self._show_folder_setting()
        self.refresh_table()
        self.schedule_rescan()

    def refresh_table(self):
        all_rc = self.db.get_receipts(self._search.text().strip())
        with_items = self.db.receipt_ids_with_items()
        if self._filter_state != "all":
            all_rc = [rc for rc in all_rc
                      if INBOX.receipt_state(rc, rc["id"] in with_items) == self._filter_state]
        self._receipts = all_rc
        t = self._table
        t.clear_rows()
        t.setRowCount(len(self._receipts))
        for r, rc in enumerate(self._receipts):
            t.set_item(r, 0, fmt_date(rc.get("receipt_date") or rc.get("uploaded_at", "")[:10]))
            t.set_item(r, 1, rc.get("vendor") or "—", bold=bool(rc.get("vendor")))
            t.set_item(r, 2, rc.get("original_name") or "(manual)")
            t.money_item(r, 3, -float(rc.get("total") or 0) if rc.get("total") else 0.0)
            label, color = _STATUS.get(rc.get("status", "new"), ("New", WARNING))
            if INBOX.receipt_state(rc, rc["id"] in with_items) == INBOX.NEEDS_PARSING:
                label = "Not parsed"
            t.set_item(r, 4, label, color=color, bold=True)
            t.setCellWidget(r, 5, self._actions(rc))
            t.setRowHeight(r, ROW_H)
        n = len(self._receipts)
        pending = sum(1 for x in self._receipts if x.get("status") != "booked")
        shown = "" if self._filter_state == "all" else f" ({self._filter.currentText().lower()})"
        self._count.setText(f"{n} receipt{'s' if n != 1 else ''}{shown}  ·  {pending} not yet "
                            "saved to the books  ·  double-click to review")
        self._queue_btn.setText(f"Review Queue ({len(self.review_queue())})")

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
        if self.db.read_only:
            de.setEnabled(False)
            de.setToolTip(READ_ONLY_TIP)
        lay.addWidget(de)
        return cell

    def _on_filter(self, *_):
        self._filter_state = self._filter.currentData() or "all"
        self.refresh_table()

    def set_filter(self, state: str):
        ix = self._filter.findData(state)
        if ix >= 0:
            self._filter.setCurrentIndex(ix)

    def _apply_read_only(self):
        ro = bool(self.db.read_only)
        for w in (self._add_btn, self._manual_btn, self._parse_btn):
            w.setEnabled(not ro)
            w.setToolTip(READ_ONLY_TIP if ro else "")
        self._drop.setAcceptDrops(not ro)
        self._drop.setToolTip(READ_ONLY_TIP if ro else "")

    # ── watched folder ───────────────────────────────────────────────────────

    def folder_setting(self) -> dict:
        return db_config.get_receipt_folder(self.db.db_path)

    def _show_folder_setting(self):
        s = self.folder_setting()
        has = bool(s["path"])
        if has:
            self._folder_lbl.setText(f"<b>Receipts folder:</b> {_html(s['path'])}")
            self._folder_lbl.setToolTip(s["path"])
        else:
            self._folder_lbl.setText(
                "<b>Keep downloaded receipts in a folder?</b> Choose it and FinanceBook shows "
                "what's new and what still needs review. (This is set on each computer.)")
            self._folder_lbl.setToolTip("")
        self._choose_btn.setText("Choose…" if has else "Choose Folder…")
        for w in (self._rescan_btn, self._clear_btn, self._subfolders_chk):
            w.setVisible(has)
        self._subfolders_chk.blockSignals(True)
        self._subfolders_chk.setChecked(s["subfolders"])
        self._subfolders_chk.blockSignals(False)
        if not has:
            self.scan = None
            self._status_lbl.hide()
            self._banner.hide()

    def _start_dir(self) -> str:
        last = db_config.get_value("last_receipt_folder_dir")
        if last and os.path.isdir(last):
            return last
        home = os.path.expanduser("~")
        for name in ("Downloads", "Documents"):
            p = os.path.join(home, name)
            if os.path.isdir(p):
                return p
        return home

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Your Receipts Folder",
                                                  self._start_dir())
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder: str, subfolders: bool | None = None):
        if subfolders is None:
            subfolders = self.folder_setting()["subfolders"]
        db_config.set_receipt_folder(self.db.db_path, folder, subfolders)
        self._dismissed = None
        self._show_folder_setting()
        self.rescan()

    def clear_folder(self):
        db_config.clear_receipt_folder(self.db.db_path)
        self._cancel_scan()
        self._show_folder_setting()

    def _on_subfolders(self, on: bool):
        s = self.folder_setting()
        if s["path"]:
            db_config.set_receipt_folder(self.db.db_path, s["path"], on)
            self.rescan()

    def schedule_rescan(self):
        if self.folder_setting()["path"]:
            self._rescan_timer.start()

    def _on_app_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive and self.isVisible():
            self.schedule_rescan()

    def _cancel_scan(self):
        self._scan_token += 1
        if self._scan_worker is not None:
            self._scan_worker.cancelled.set()

    def rescan(self):
        s = self.folder_setting()
        if not s["path"]:
            return
        self._cancel_scan()                     # a newer scan replaces a stale one
        receipts = self.db.get_receipts()
        db = self.db

        def copy_size(rc):
            try:
                return os.stat(db.receipt_abs_path(rc)).st_size
            except OSError:
                return None
        w = ScanWorker(s["path"], s["subfolders"], receipts, copy_size,
                       self.db.receipt_ids_with_items(), self._scan_token)
        self._scan_worker = w
        _RUNNING.add(w)
        w.done.connect(self._scan_done)
        w.finished.connect(lambda w=w: _RUNNING.discard(w))
        self._status_lbl.setText("Checking the receipts folder…")
        self._status_lbl.show()
        w.start()

    def _scan_done(self, res, token):
        if token != self._scan_token or res.cancelled:
            return
        self.scan = res
        # An exception escaping a slot aborts a PyQt6 app, and the database
        # can go away under a late scan result (switched, or a sync hiccup).
        try:
            self._show_status(res)
            self._show_banner(res)
        except Exception as e:
            self._banner.hide()
            self._status_lbl.setText(f"⚠ The receipts folder status couldn't be updated "
                                     f"({type(e).__name__}). Press Rescan to try again.")

    def _show_status(self, res: INBOX.ScanResult):
        self._status_lbl.show()
        if res.error:
            self._status_lbl.setText(f"⚠ {_html(res.error)}")
            return
        n = len(res.files)
        parts = [f"{n} file{'s' if n != 1 else ''} in folder", f"{res.imported} imported",
                 f"{res.count(INBOX.NEW)} new"]
        need_parse = res.count(INBOX.NEEDS_PARSING)
        need_review = res.count(INBOX.NEEDS_REVIEW)
        if need_parse:
            parts.append(_link(INBOX.NEEDS_PARSING, f"{need_parse} not parsed yet"))
        parts.append(_link(INBOX.NEEDS_REVIEW, f"{need_review} imported but not reviewed")
                     if need_review else "0 imported but not reviewed")
        if res.count(INBOX.DRAFT):
            d = res.count(INBOX.DRAFT)
            parts.append(f"{d} draft{'s' if d != 1 else ''}")
        saved = res.count(INBOX.SAVED)
        parts.append(_link(INBOX.SAVED, f"{saved} saved to books") if saved
                     else "0 saved to books")
        extra = []
        if res.online_only_new:
            extra.append(f"{res.online_only_new} file{'s are' if res.online_only_new != 1 else ' is'}"
                         " online-only and will download when imported")
        dups = sum(1 for f in res.files if f.renamed_duplicate)
        if dups:
            extra.append(f"{dups} {'are copies' if dups != 1 else 'is a copy'} of receipts "
                         "already on file under another name")
        if res.unsupported:
            extra.append(f"{len(res.unsupported)} other file{'s' if len(res.unsupported) != 1 else ''}"
                         " ignored (not receipts)")
        text = " · ".join(parts)
        if extra:
            text += "<br>" + " · ".join(extra)
        self._status_lbl.setText(text)

    def _on_status_link(self, href: str):
        self._tabs.setCurrentIndex(0)
        self.set_filter(href)

    def _show_banner(self, res: INBOX.ScanResult):
        to_parse = res.count(INBOX.NEW) + res.count(INBOX.NEEDS_PARSING)
        queue = len(self.review_queue())
        # Two files with the same content are one receipt to review.
        waiting = len({f.receipt_id for f in res.files if f.state == INBOX.NEEDS_REVIEW})
        if res.error or (not to_parse and not waiting) \
                or self._dismissed == res.signature():
            self._banner.hide()
            return
        bits = []
        if res.count(INBOX.NEW):
            bits.append(f"{res.count(INBOX.NEW)} new receipt"
                        f"{'s' if res.count(INBOX.NEW) != 1 else ''} in your folder")
        if res.count(INBOX.NEEDS_PARSING):
            bits.append(f"{res.count(INBOX.NEEDS_PARSING)} not parsed yet")
        if waiting:
            bits.append(f"{waiting} receipt{'s' if waiting != 1 else ''} waiting for review")
        self._banner_lbl.setText(" · ".join(bits) + ".")
        self._parse_btn.setText(f"Parse {to_parse} new" if to_parse else "Parse")
        self._parse_btn.setVisible(bool(to_parse))
        self._banner_review_btn.setText(f"Review ({queue})" if queue else "Review")
        self._banner_review_btn.setVisible(bool(queue))
        self._apply_read_only()
        self._banner.show()

    def _not_now(self):
        if self.scan is not None:
            self._dismissed = self.scan.signature()
        self._banner.hide()

    # ── batch import + parse ─────────────────────────────────────────────────

    def parse_inbox(self):
        """Import the folder's new files and parse them (plus imported ones never parsed)."""
        if self.db.read_only or self.scan is None:
            return
        paths = [f.path for f in self.scan.files if f.state == INBOX.NEW]
        rids = [f.receipt_id for f in self.scan.files if f.state == INBOX.NEEDS_PARSING]
        if not paths and not rids:
            return
        self._run_batch(INBOX.import_and_parse, "Reading receipts…", self._batch_done,
                        db=self.db, paths=paths, receipt_ids=rids)

    def _run_batch(self, fn, label, then, **kwargs):
        total = len(kwargs.get("paths", ())) + len(kwargs.get("receipt_ids", ()))
        prog = QProgressDialog(label, "Cancel", 0, max(total, 1), self)
        prog.setWindowTitle("Receipts")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        w = BatchWorker(fn, **kwargs)
        _RUNNING.add(w)
        self.last_worker = w
        self.batch_progress = prog

        def on_progress(done, tot, name):
            prog.setMaximum(max(tot, 1))
            prog.setValue(done)
            if name:
                prog.setLabelText(f"{label}  {done + 1} of {tot}\n{name}")

        def on_cancel():
            w.cancelled.set()
            prog.setLabelText("Stopping after this file…")

        def finished(summary):
            try:
                prog.close()
                then(summary)
            except RuntimeError:     # the page was closed (e.g. database switched) meanwhile
                pass
            except Exception as e:   # never let a slot exception abort the app
                QMessageBox.warning(self, "Receipts", f"{summary.text()}.\n\nThe list couldn't "
                                    f"be refreshed ({type(e).__name__}).")
        w.progress.connect(on_progress)
        prog.canceled.connect(on_cancel)
        w.done.connect(finished)
        w.finished.connect(lambda w=w: _RUNNING.discard(w))
        w.start()

    def _batch_done(self, s: INBOX.BatchSummary):
        self.last_summary = s
        self._parsed_cache.update(s.parsed)
        self.refresh_table()
        details = ""
        if s.duplicates:
            details += "\n\nAlready on file (not imported again):\n• " + "\n• ".join(s.duplicates)
        if s.failed:
            details += "\n\nCouldn't read:\n• " + "\n• ".join(f"{n} — {why}" for n, why in s.failed)
        QMessageBox.information(self, "Receipts Imported", s.text() + "." + details)
        if s.need_ai:
            self._offer_ai(s.need_ai)
        self.rescan()

    def _offer_ai(self, rids: list):
        n = len(rids)
        if QMessageBox.question(
                self, "Read with AI?",
                f"{n} receipt{'s' if n != 1 else ''} had no readable text (photos or scanned "
                "PDFs). They can be read with AI, or you can type them in when you review "
                "them.\n\nRead them with AI now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        s = db_config.ai_settings()
        if not s["enabled"] or not s["api_key"]:
            QMessageBox.information(
                self, "AI Receipt Reading Is Off",
                "AI receipt reading is optional. Turn it on and paste your Anthropic API key in "
                "Settings → AI Receipt Reading, then use Read with AI when you review them.")
            return
        names = [(self.db.get_receipt(r) or {}).get("original_name") or r for r in rids]
        if QMessageBox.question(
                self, "Send to Anthropic?",
                f"This uploads these {n} file{'s' if n != 1 else ''} to Anthropic's API, using "
                "your API key — each one is billed to your Anthropic account and leaves this "
                "computer. Nothing else from your books is sent.\n\n• " +
                "\n• ".join(names[:15]) + ("\n…" if n > 15 else "") + "\n\nSend them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        db_config.set_flag("ai_receipts_notice_ack", True)
        self._run_batch(INBOX.read_with_ai, "Reading with AI…", self._ai_batch_done,
                        db=self.db, receipt_ids=rids, api_key=s["api_key"], model=s["model"])

    def _ai_batch_done(self, s: INBOX.BatchSummary):
        self._parsed_cache.update(s.parsed)
        self.refresh_table()
        msg = f"Read {len(s.reparsed)} with AI"
        if s.failed:
            msg += " · couldn't read:\n• " + "\n• ".join(f"{n} — {why}" for n, why in s.failed)
        if s.cancelled:
            msg += f"\n(stopped after {s.done} of {s.total})"
        QMessageBox.information(self, "Read with AI", msg)
        self.rescan()

    # ── review queue ─────────────────────────────────────────────────────────

    def review_queue(self) -> list[dict]:
        """Receipts that still need parsing or review, oldest first."""
        with_items = self.db.receipt_ids_with_items()
        out = [rc for rc in self.db.get_receipts()
               if INBOX.receipt_state(rc, rc["id"] in with_items) in
               (INBOX.NEEDS_PARSING, INBOX.NEEDS_REVIEW)]
        out.sort(key=lambda rc: (rc.get("uploaded_at") or "", rc["id"]))
        return out

    def start_review_queue(self):
        queue = self.review_queue()
        if not queue:
            QMessageBox.information(self, "Review Queue", "Nothing is waiting for review.")
            return
        self._queue = queue
        self._queue_at = 0
        self._open_queue_item()

    def _open_queue_item(self):
        i, n = self._queue_at, len(self._queue)
        self._review(self._queue[i], queue_label=f"{i + 1} of {n}",
                     on_done=self._queue_item_done)

    def _queue_item_done(self, accepted: bool):
        i, n = self._queue_at, len(self._queue)
        if not accepted or i + 1 >= n:
            self._queue = []
            if accepted and i + 1 >= n:
                QMessageBox.information(self, "Review Queue", "That was the last one.")
            return
        if not self.ask_next(i + 2, n):
            self._queue = []
            return
        self._queue_at = i + 1
        self._open_queue_item()

    def ask_next(self, pos: int, n: int) -> bool:
        return QMessageBox.question(
            self, "Next Receipt", f"Open the next receipt ({pos} of {n})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    # ── actions ──────────────────────────────────────────────────────────────

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Receipts", os.path.expanduser("~"),
                                                FILE_FILTER)
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]):
        if self.db.read_only:
            return
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
        self.refresh_table()
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
        _RUNNING.add(w)
        self.last_worker = w

        def finished(results):
            try:
                prog.close()
                then(results)
            except RuntimeError:     # the page was closed (e.g. database switched) meanwhile
                pass
        w.done.connect(finished)
        w.finished.connect(lambda w=w: _RUNNING.discard(w))
        w.start()

    def _parsed_new(self, results):
        added = []
        for rid, parsed in results:
            self.db.save_receipt(dict(receipt_fields(parsed), id=rid))
            rc = self.db.get_receipt(rid)
            if rc:
                added.append((rc, parsed))
        self.refresh_table()
        self.schedule_rescan()
        if len(added) == 1:
            self._review(*added[0])

    def _manual(self):
        if self.db.read_only:
            return
        rc = self.db.add_manual_receipt()
        self.refresh_table()
        self._review(rc)

    def _review(self, rc, parsed=None, queue_label: str = "", on_done=None):
        from ui.dialogs.receipt_dialog import ReceiptReviewDialog
        rc = self.db.get_receipt(rc["id"]) or rc
        if parsed is None:
            parsed = self._parsed_cache.get(rc["id"])
        if parsed is None and not self.db.get_receipt_items(rc["id"]) \
                and rc.get("status") != "booked" and rc.get("file_path"):
            self._parse_async([(rc["id"], self.db.receipt_abs_path(rc))],
                              lambda res, rc=rc: self._review(rc, res[0][1], queue_label,
                                                              on_done))
            return
        dlg = ReceiptReviewDialog(self.db, rc, parent=self, parsed=parsed,
                                  queue_label=queue_label)
        self.last_dialog = dlg
        accepted = dlg.exec() == ReceiptReviewDialog.DialogCode.Accepted
        if accepted:
            self._parsed_cache.pop(rc["id"], None)
        self.refresh_table()
        self.schedule_rescan()
        if on_done:
            on_done(accepted)

    def _delete(self, rc):
        if self.db.read_only:
            return
        booked = rc.get("status") == "booked"
        msg = (f"Delete the receipt “{rc.get('vendor') or rc.get('original_name') or 'manual'}”"
               " and its copied file?")
        if booked:
            msg += "\n\nThe transaction it created stays in your books."
        if QMessageBox.question(self, "Delete Receipt", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db.delete_receipt(rc["id"])
            self.refresh_table()
            self.schedule_rescan()

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
            upp = int(it.get("units_per_pack") or 1)
            units = float(it.get("qty") or 1) * upp
            t.set_item(r, 4, f"{units:g}" + (f"  ({upp}/pack)" if upp > 1 else ""),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 5, fmt(it.get("unit_price")),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.set_item(r, 6, fmt(it.get("line_total")),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, bold=True)
            t.set_item(r, 7, cats.get(it.get("category_id", ""), "—"))
        total = money_sum(i.get("line_total") for i in rows)
        self._items_sum.setText(f"{len(rows)} matching item{'s' if len(rows) != 1 else ''}  ·  "
                                f"Total spent: {fmt(total)}")


def _html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _link(href: str, text: str) -> str:
    return f'<a href="{href}">{text}</a>'
