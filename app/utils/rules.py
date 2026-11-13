"""
Bank-rule conditions — Qt-free so matching and the rule text are unit-tested.

A rule has one or more conditions and a match mode: "all" (every condition must
hold) or "any" (at least one). Conditions are stored in the rule_conditions
table as {field, operator, value, value2}. Rules saved before v1.14 had a
single condition in the rules row itself; the schema-12 migration copies it
into one rule_conditions row, and legacy_condition() rebuilds it for a rule
dict that has none (e.g. built in code by an older caller).
"""

FIELDS = (
    ("payee", "Payee / Description"),
    ("memo", "Memo"),
    ("amount", "Amount ($)"),
    ("date", "Date"),
    ("direction", "Money in / out"),
    ("account", "Bank account"),
)
FIELD_LABELS = dict(FIELDS)

_TEXT_OPS = (("contains", "contains"), ("equals", "equals (exact)"),
             ("starts_with", "starts with"), ("ends_with", "ends with"))
OPERATORS = {
    "payee": _TEXT_OPS,
    "memo": _TEXT_OPS,
    "amount": (("equals", "= equals"), ("gt", "> greater than"),
               ("lt", "< less than"), ("between", "between")),
    "date": (("equals", "= specific date (YYYY-MM-DD)"),
             ("day_of_month", "day of month (1 – 31)")),
    "direction": (("is", "is"),),
    "account": (("is", "is"),),
}
DIRECTIONS = (("out", "Money out (spending)"), ("in", "Money in (deposit)"))

_OP_TEXT = {"contains": "contains", "equals": "is", "starts_with": "starts with",
            "ends_with": "ends with", "gt": ">", "lt": "<", "between": "between",
            "day_of_month": "day of month is", "is": "is"}
_SHORT_FIELD = {"payee": "Payee", "memo": "Memo", "amount": "Amount", "date": "Date",
                "direction": "Money", "account": "Bank account"}

MATCH_MODES = ("all", "any")


def legacy_condition(rule: dict) -> dict:
    """The single condition a pre-v1.14 rule row carries."""
    return {"field": rule.get("field") or "payee",
            "operator": rule.get("operator") or "contains",
            "value": str(rule.get("value") or ""),
            "value2": str(rule.get("value2") or "")}


def conditions_of(rule: dict) -> list:
    conds = rule.get("conditions")
    if conds is None:
        return [legacy_condition(rule)]
    return list(conds)


def condition_matches(cond: dict, txn: dict) -> bool:
    field = cond.get("field") or "payee"
    op = cond.get("operator") or ""
    value = str(cond.get("value") or "")
    value2 = str(cond.get("value2") or "")

    if field in ("payee", "memo"):
        if field == "payee":
            text = (txn.get("payee") or txn.get("memo") or "").lower()
        else:
            text = (txn.get("memo") or "").lower()
        v = value.lower()
        if not v:
            return False
        if op == "contains":
            return v in text
        if op == "equals":
            return text == v
        if op == "starts_with":
            return text.startswith(v)
        if op == "ends_with":
            return text.endswith(v)
        return False
    if field == "amount":
        try:
            amt = abs(float(txn.get("amount") or 0))
            v = float(value)
            if op == "equals":
                return abs(amt - v) < 0.005
            if op == "gt":
                return amt > v
            if op == "lt":
                return amt < v
            if op == "between":
                v2 = float(value2)
                return min(v, v2) <= amt <= max(v, v2)
        except (ValueError, TypeError):
            return False
        return False
    if field == "date":
        date_str = txn.get("date", "") or ""
        if op == "equals":
            return date_str == value
        if op == "day_of_month":
            try:
                return int(date_str.split("-")[2]) == int(value)
            except (IndexError, ValueError):
                return False
        return False
    if field == "direction":
        try:
            amt = float(txn.get("amount") or 0)
        except (ValueError, TypeError):
            return False
        if value == "in":
            return amt > 0
        if value == "out":
            return amt < 0
        return False
    if field == "account":
        return bool(value) and (txn.get("account_id") or "") == value
    return False


def rule_matches(rule: dict, txn: dict) -> bool:
    conds = conditions_of(rule)
    if not conds:
        return False
    results = (condition_matches(c, txn) for c in conds)
    if (rule.get("match_mode") or "all") == "any":
        return any(results)
    return all(results)


def describe_condition(cond: dict, account_names: dict | None = None) -> str:
    field = cond.get("field") or "payee"
    op = cond.get("operator") or ""
    value = str(cond.get("value") or "")
    if field == "direction":
        return "Money in" if value == "in" else "Money out"
    if field == "account":
        return f"Bank account is {(account_names or {}).get(value, value or '?')}"
    if field == "amount" and op == "between":
        return f"Amount between {value} and {cond.get('value2') or ''}"
    label = _SHORT_FIELD.get(field, field)
    shown = f"“{value}”" if field in ("payee", "memo") else value
    return f"{label} {_OP_TEXT.get(op, op)} {shown}"


def describe_rule(rule: dict, account_names: dict | None = None) -> str:
    """e.g. 'Payee contains “COSTCO” AND Amount > 50'."""
    joiner = " OR " if (rule.get("match_mode") or "all") == "any" else " AND "
    return joiner.join(describe_condition(c, account_names) for c in conditions_of(rule))
