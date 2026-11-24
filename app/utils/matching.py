"""
The one matching engine for imported bank rows and for the Transfers page.

No Qt here: the Import page's "Match" column, Database.bulk_save_transactions
(mirror adoption) and Transfers → Potential Transfers all call this module, so
the three always agree.

find_matches(db, rows, account_id) looks at rows about to be imported into
account_id and returns one RowMatch per row. Kinds, checked in this order:

  1. MIRROR    a transfer side FinanceBook created (is_transfer=1, a pair id,
               no import hash) in the SAME account with the SAME signed amount.
               The bank row takes its place.
  2. MANUAL    a hand-entered transaction in the SAME account (no import hash,
               not a transfer) with the SAME signed amount. The bank row merges
               into it and keeps the user's category, class, payee and notes.
  3. TRANSFER  an unlinked transaction in ANOTHER of the user's accounts with
               the exact OPPOSITE amount. On import both become one transfer.
     RULE      instead of 3 when a rule already sends the row to a balance-sheet
               account (row["_transfer_to"]): only that account is searched, and
               with no candidate the rule still makes the transfer (new mirror).

Everything is within TRANSFER_MATCH_DAYS (3) of the row's date. Never matched:
reconciled transactions, anything dated on/before the period lock date, and
rows whose import_hash is already in the database (duplicates).

A split (v1.19: one transaction with several account lines) is a candidate
as ONE unit — its total, its first line's id — and only as MANUAL (a
hand-entered split in the same account with the same total and no import
hash yet). It is never a transfer or mirror candidate.

One-to-one: each existing transaction is used by at most one row. Every
(row, candidate) pair is ranked by kind (mirror > manual > transfer), then
closer date, then description hints ("transfer", "payment", the other
account's name or last-4 digits …), and assigned greedily best-first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

MATCH_DAYS = 3          # same as Database.TRANSFER_MATCH_DAYS

MIRROR = "mirror"
MANUAL = "manual"
TRANSFER = "transfer"
RULE = "rule_transfer"
KIND_RANK = {MIRROR: 0, MANUAL: 1, TRANSFER: 2, RULE: 2}

HINT_WORDS = ("transfer", "xfer", "payment", "thank you", "autopay", "zelle")

# Words in an account name too generic to say "this description is about
# that account" ("Chase Checking" vs a description that just says "checking").
_GENERIC = {"the", "and", "account", "acct", "card", "checking", "savings",
            "saving", "credit", "debit", "bank", "my", "personal", "business",
            "main", "joint", "cash", "loan"}


@dataclass
class Candidate:
    txn: dict
    kind: str
    days: int
    hints: int
    score: int          # 0–100, for display: same day 100, −15 a day, +10 a hint

    @property
    def id(self) -> str:
        return self.txn["id"]


@dataclass
class RowMatch:
    index: int                          # position in the rows passed in
    kind: str = ""                      # "" = no match
    candidates: list = field(default_factory=list)   # best first, all usable
    chosen: Candidate | None = None     # the default pick (one candidate only)
    rule_account: str = ""              # RULE: the rule's account

    @property
    def confident(self) -> bool:
        return len(self.candidates) == 1


def _d(iso) -> date | None:
    try:
        return date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


def _amt(t) -> float:
    try:
        return round(float(t.get("amount") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _cents(v: float) -> int:
    return int(round(v * 100))


def _desc(t: dict) -> str:
    return f"{t.get('payee') or ''} {t.get('memo') or ''}".lower()


def account_tokens(acct: dict) -> set:
    """Words (and 4-digit runs, e.g. a card's last 4) of an account's name that
    identify it in a bank description."""
    name = f"{acct.get('name') or ''}".lower()
    toks = {w for w in re.findall(r"[a-z]{3,}", name) if w not in _GENERIC}
    toks |= set(re.findall(r"\d{4}", name))
    return toks


def hint_count(text: str, other_account: dict | None) -> int:
    """0–2: one for a transfer word, one for naming the other account."""
    text = text.lower()
    n = 1 if any(w in text for w in HINT_WORDS) else 0
    if other_account:
        words = set(re.findall(r"[a-z]{3,}", text)) | set(re.findall(r"\d{4}", text))
        if words & account_tokens(other_account):
            n += 1
    return n


def _score(days: int, hints: int) -> int:
    return max(0, min(100, 100 - 15 * days + 10 * hints))


def _pool(db, lo: str, hi: str) -> list[dict]:
    """Transactions in [lo, hi] that may ever be matched: not reconciled,
    after the lock date, in an account that exists. A split group is one
    entry (its total, first line's id, "split_group_id" set); a group with
    any reconciled line is left out."""
    lock = ""
    try:
        lock = db.lock_date() or ""
    except Exception:
        pass
    q = ("SELECT t.* FROM transactions t JOIN accounts a ON a.id=t.account_id "
         "WHERE t.date BETWEEN ? AND ? ")
    p: list = [lo, hi]
    if lock:
        q += " AND t.date > ?"
        p.append(lock)
    q += " ORDER BY t.created_at, t.rowid"
    with db._conn() as c:
        raw = [dict(r) for r in c.execute(q, p).fetchall()]
    rows, groups = [], {}
    for r in raw:
        gid = r.get("split_group_id") or ""
        if gid:
            groups.setdefault(gid, []).append(r)
        elif not int(r.get("reconciled") or 0) and abs(float(r.get("amount") or 0)) >= 0.005:
            rows.append(r)
    for gid, lines in groups.items():
        if any(int(l.get("reconciled") or 0) for l in lines):
            continue
        head = dict(lines[0])
        head["amount"] = round(sum(float(l.get("amount") or 0) for l in lines), 2)
        if abs(head["amount"]) < 0.005:
            continue
        head["import_hash"] = next((l["import_hash"] for l in lines if l.get("import_hash")), "")
        head["is_transfer"] = 0
        head["transfer_pair_id"] = ""
        rows.append(head)
    for r in rows:
        r["is_transfer"] = str(int(r.get("is_transfer") or 0))
        r["reconciled"] = str(int(r.get("reconciled") or 0))
        r["amount"] = str(float(r.get("amount") or 0))
    return rows


def _kind_for(row_amt: float, account_id: str, rule_acct: str, t: dict) -> str:
    """How existing transaction t could match a row (amount row_amt) being
    imported into account_id, or "" if it can't."""
    same_acct = t.get("account_id") == account_id
    is_tr = str(t.get("is_transfer", "0")) == "1"
    hashed = bool(t.get("import_hash"))
    amt = _amt(t)
    if t.get("split_group_id"):
        # A split matches only as the user's own entry for this bank row.
        if same_acct and not hashed and abs(amt - row_amt) < 0.005:
            return MANUAL
        return ""
    if same_acct:
        if abs(amt - row_amt) >= 0.005 or hashed:
            return ""
        if is_tr:
            return MIRROR if t.get("transfer_pair_id") else ""
        return MANUAL
    if is_tr or abs(amt + row_amt) >= 0.005:
        return ""
    if rule_acct:
        return RULE if t.get("account_id") == rule_acct else ""
    return TRANSFER


def find_matches(db, rows: list[dict], account_id: str,
                 kinds=(MIRROR, MANUAL, TRANSFER, RULE)) -> list[RowMatch]:
    """One RowMatch per row (same order). See the module docstring."""
    out = [RowMatch(index=i) for i in range(len(rows))]
    for i, r in enumerate(rows):
        if r.get("_transfer_to") and r["_transfer_to"] != account_id and RULE in kinds:
            out[i].kind = RULE
            out[i].rule_account = r["_transfer_to"]
    dates = [_d(r.get("date")) for r in rows]
    known = [d for d in dates if d]
    if not known:
        return out
    lo = (min(known) - timedelta(days=MATCH_DAYS)).isoformat()
    hi = (max(known) + timedelta(days=MATCH_DAYS)).isoformat()
    pool = _pool(db, lo, hi)
    by_abs: dict = {}
    for t in pool:
        by_abs.setdefault(abs(_cents(_amt(t))), []).append(t)
    accounts = {a["id"]: a for a in db.get_accounts(active=None)}
    existing = db.get_existing_hashes()
    lock = ""
    try:
        lock = db.lock_date() or ""
    except Exception:
        pass

    edges = []          # (rank key, row index, Candidate)
    seen_hashes = set()
    for i, r in enumerate(rows):
        h = r.get("import_hash") or ""
        d0 = dates[i]
        if (h and (h in existing or h in seen_hashes)) or d0 is None \
                or (lock and d0.isoformat() <= lock):
            if h:
                seen_hashes.add(h)
            continue        # a duplicate / undated / locked row: never matched
        if h:
            seen_hashes.add(h)
        row_amt = _amt(r)
        if not row_amt:
            continue
        rule_acct = out[i].rule_account
        for t in by_abs.get(abs(_cents(row_amt)), ()):
            kind = _kind_for(row_amt, account_id, rule_acct, t)
            if not kind or kind not in kinds:
                continue
            td = _d(t.get("date"))
            if td is None:
                continue
            days = abs((td - d0).days)
            if days > MATCH_DAYS:
                continue
            if kind in (TRANSFER, RULE):
                hints = max(hint_count(_desc(r), accounts.get(t["account_id"])),
                            hint_count(_desc(t), accounts.get(account_id)))
            else:
                hints = 0
            cand = Candidate(t, kind, days, hints, _score(days, hints))
            key = (KIND_RANK[kind], days, -hints, i, t.get("date") or "",
                   t.get("created_at") or "", t["id"])
            edges.append((key, i, cand))

    edges.sort(key=lambda e: e[0])
    taken: dict = {}            # candidate id → row index
    best: dict = {}             # row index → Candidate
    for _key, i, cand in edges:
        if i in best or cand.id in taken:
            continue
        best[i] = cand
        taken[cand.id] = i

    for i, m in enumerate(out):
        b = best.get(i)
        if b is None:
            continue
        # Alternatives: same kind, not claimed by another row, best first.
        alts = [c for _k, j, c in edges
                if j == i and c.kind == b.kind and taken.get(c.id, i) == i]
        m.kind = b.kind
        m.candidates = alts
        m.chosen = b if len(alts) == 1 else None
    return out


def find_transfer_pairs(db, txns: list[dict] | None = None) -> list[dict]:
    """Unlinked transactions in two different accounts that look like the two
    sides of one transfer — same rules as find_matches' TRANSFER kind (exact
    opposite amount, within MATCH_DAYS, nothing reconciled / split / locked /
    already a transfer), ranked the same way and one-to-one.

    Returns [{"txn_a": money-out side, "txn_b": money-in side, "amount",
    "days", "score"}] best first."""
    if txns is None:
        pool = _pool(db, "0000-00-00", "9999-99-99")
    else:
        ids = {t["id"] for t in txns}
        pool = [t for t in _pool(db, "0000-00-00", "9999-99-99") if t["id"] in ids]
    pool = [t for t in pool if str(t.get("is_transfer", "0")) == "0"
            and not t.get("split_group_id")]
    accounts = {a["id"]: a for a in db.get_accounts(active=None)}
    outs: dict = {}
    for t in pool:
        a = _amt(t)
        if a < 0:
            outs.setdefault(-_cents(a), []).append(t)
    edges = []
    for t in pool:
        a = _amt(t)
        if a <= 0:
            continue
        di = _d(t.get("date"))
        if di is None:
            continue
        for o in outs.get(_cents(a), ()):
            if o["account_id"] == t["account_id"]:
                continue
            do = _d(o.get("date"))
            if do is None:
                continue
            days = abs((di - do).days)
            if days > MATCH_DAYS:
                continue
            hints = max(hint_count(_desc(o), accounts.get(t["account_id"])),
                        hint_count(_desc(t), accounts.get(o["account_id"])))
            key = (days, -hints, o.get("date") or "", o["id"], t["id"])
            edges.append((key, o, t, days, hints))
    edges.sort(key=lambda e: e[0])
    used, pairs = set(), []
    for _k, o, t, days, hints in edges:
        if o["id"] in used or t["id"] in used:
            continue
        used.update((o["id"], t["id"]))
        pairs.append({"txn_a": o, "txn_b": t, "amount": abs(_amt(o)),
                      "days": days, "score": _score(days, hints)})
    return pairs
