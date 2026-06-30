"""
Parse OFX, QFX (Quicken) and QBO (QuickBooks Online) files.
All three are the same OFX/SGML format — ofxparse handles them identically.
"""
import hashlib
from datetime import datetime
from ofxparse import OfxParser


def parse_ofx(file_obj, account_id: str) -> list[dict]:
    """Parse a QFX or QBO file and return a list of transaction dicts."""
    ofx = OfxParser.parse(file_obj)
    results = []

    for account in ofx.accounts:
        for txn in account.statement.transactions:
            date_str = txn.date.strftime("%Y-%m-%d") if txn.date else ""
            amount = float(txn.amount)
            payee = str(txn.payee or txn.memo or "").strip()
            memo = str(txn.memo or "").strip()
            fitid = str(txn.id or "")

            # Dedup hash based on fitid + date + amount
            raw = f"{fitid}|{date_str}|{amount}"
            import_hash = hashlib.md5(raw.encode()).hexdigest()[:16]

            results.append({
                "date": date_str,
                "account_id": account_id,
                "payee": payee,
                "memo": memo,
                "amount": str(amount),
                "category_id": "",
                "class_id": "",
                "is_transfer": "0",
                "transfer_pair_id": "",
                "reconciled": "0",
                "reconcile_id": "",
                "notes": "",
                "import_hash": import_hash,
            })

    return results
