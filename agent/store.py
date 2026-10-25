"""Shared state for the agent and channel scripts: paths, action folders, the mock CRM and out/summary.json."""
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
ACTIONS = OUT / "actions"
QUEUE = OUT / "queue.json"
SUMMARY = OUT / "summary.json"
CRM_DB = ROOT / "agent" / "crm_mock" / "crm.sqlite"
MODEL_ENV = ROOT / "platform" / "model.env"

EDITABLE = ("customer_email.md", "customer_slack.md", "internal_note.md", "writeoff_memo.md")
HUMAN_STATES = ("approved", "edited")
APPROVER_LINE = "Approver: ______________________    Date: ____________"
INVOICE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
LOCK_STALE_SECONDS = 600


def now():
    return datetime.now().isoformat(timespec="seconds")


def money(amount, currency="USD"):
    return f"${amount:,.2f}" if currency == "USD" else f"{amount:,.2f} {currency}"


def long_date(value):
    d = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{d:%B} {d.day}, {d.year}"


def read_env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def model_config():
    env = read_env_file(MODEL_ENV)
    base_url = os.environ.get("MODEL_BASE_URL") or env.get("MODEL_BASE_URL") or "http://127.0.0.1:11434/v1"
    model = os.environ.get("MODEL_NAME") or env.get("MODEL_NAME") or "qwen3:8b"
    return base_url, model


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_text(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2) + "\n")


def load_queue(path=QUEUE):
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"{path} does not exist yet. Run python3 triage/triage.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot_id(queue):
    return queue.get("snapshot_id") or queue["as_of"]


def action_dir(invoice_id):
    if not INVOICE_ID.match(invoice_id):
        raise ValueError(f"bad invoice id {invoice_id!r}")
    return ACTIONS / invoice_id


def read_status(invoice_id):
    return read_json(action_dir(invoice_id) / "status.json")


def all_statuses():
    if not ACTIONS.exists():
        return {}
    statuses = {}
    for d in ACTIONS.iterdir():
        status = read_json(d / "status.json") if d.is_dir() else None
        if status:
            statuses[d.name] = status
    return statuses


def load_action(invoice_id):
    d = action_dir(invoice_id)
    status = read_json(d / "status.json")
    if status is None:
        return None
    return {
        "status": status,
        "case": read_json(d / "salesforce_case.json", {}),
        "item": read_json(d / "queue_item.json", {}),
        "files": {name: (d / name).read_text(encoding="utf-8") for name in EDITABLE if (d / name).exists()},
    }


def claim(invoice_id):
    """Take the per-invoice drafting lock so overlapping runs never draft the same invoice twice."""
    ACTIONS.mkdir(parents=True, exist_ok=True)
    lock = ACTIONS / f"{invoice_id}.lock"
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()
    except FileNotFoundError:
        pass
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def release(invoice_id):
    (ACTIONS / f"{invoice_id}.lock").unlink(missing_ok=True)


def set_state(invoice_id, state, by, edits=None):
    d = action_dir(invoice_id)
    status = read_json(d / "status.json")
    if status is None:
        raise LookupError(f"{invoice_id} has no drafts yet")
    at = now()
    for name, text in (edits or {}).items():
        if name in EDITABLE and (d / name).exists():
            write_text(d / name, text.replace("\r\n", "\n").strip() + "\n")
    memo = d / "writeoff_memo.md"
    if state == "approved" and memo.exists():
        write_text(memo, memo.read_text(encoding="utf-8").replace(APPROVER_LINE, f"Approved by {by} on {at.replace('T', ' ')}"))
    status.update(state=state, by=by, at=at)
    write_json(d / "status.json", status)
    with closing(crm()) as db, db:
        db.execute("UPDATE cases SET state = ?, updated_by = ?, updated_at = ? WHERE action_id = ?",
                   (state, by, at, status["action_id"]))
        if state == "approved" and memo.exists():
            db.execute("UPDATE invoices SET status = 'written_off', updated_at = ? WHERE invoice_id = ?", (at, invoice_id))
    return status


CRM_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY, account_id TEXT, account_name TEXT, account_owner TEXT, owner_email TEXT,
    billing_contact_email TEXT, currency TEXT, amount REAL, amount_paid REAL, balance REAL, status TEXT,
    issue_date TEXT, due_date TEXT, last_touch_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    case_id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT UNIQUE, invoice_id TEXT REFERENCES invoices(invoice_id),
    case_type TEXT, state TEXT, summary TEXT, next_action TEXT, next_action_date TEXT, model TEXT,
    created_at TEXT, updated_by TEXT, updated_at TEXT
);
"""

INVOICE_COLUMNS = ("invoice_id", "account_id", "account_name", "account_owner", "owner_email", "billing_contact_email",
                   "currency", "amount", "amount_paid", "balance", "status", "issue_date", "due_date", "last_touch_date")


def crm():
    CRM_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CRM_DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript(CRM_SCHEMA)
    return db


def crm_record(item, case):
    """Upsert the invoice and its case, return the case id."""
    at = now()
    columns = ", ".join(INVOICE_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in INVOICE_COLUMNS[1:])
    with closing(crm()) as db, db:
        db.execute(
            f"INSERT INTO invoices ({columns}, updated_at) VALUES ({', '.join('?' * len(INVOICE_COLUMNS))}, ?) "
            f"ON CONFLICT(invoice_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
            [item.get(c) for c in INVOICE_COLUMNS] + [at],
        )
        db.execute(
            "INSERT INTO cases (action_id, invoice_id, case_type, state, summary, next_action, next_action_date, model,"
            " created_at, updated_by, updated_at) VALUES (?, ?, ?, 'drafted', ?, ?, ?, ?, ?, 'agent', ?)"
            " ON CONFLICT(action_id) DO UPDATE SET state = 'drafted', summary = excluded.summary,"
            " next_action = excluded.next_action, next_action_date = excluded.next_action_date,"
            " model = excluded.model, updated_by = 'agent', updated_at = excluded.updated_at",
            (case["action_id"], case["invoice_id"], case["case_type"], case["summary"], case["next_action"],
             case["next_action_date"], case["model"], at, at),
        )
        return db.execute("SELECT case_id FROM cases WHERE action_id = ?", (case["action_id"],)).fetchone()[0]


def tally():
    """Counts and dollars of drafted packages by bucket and state, read from out/actions/."""
    counts = {b: {"drafted": 0, "awaiting_approval": 0, "approved": 0, "rejected": 0, "approved_dollars": 0.0}
              for b in ("agent", "writeoff")}
    for invoice_id, status in all_statuses().items():
        item = read_json(ACTIONS / invoice_id / "queue_item.json", {})
        c = counts.get(item.get("bucket"))
        if c is None:
            continue
        c["drafted"] += 1
        if status["state"] in ("drafted", "edited"):
            c["awaiting_approval"] += 1
        elif status["state"] == "approved":
            c["approved"] += 1
            c["approved_dollars"] += item.get("balance", 0)
        elif status["state"] == "rejected":
            c["rejected"] += 1
    for c in counts.values():
        c["approved_dollars"] = round(c["approved_dollars"], 2)
    return counts


def build_summary(queue=None, run=None):
    previous = read_json(SUMMARY, {})
    runs = previous.get("runs", [])
    if run:
        runs = (runs + [run])[-50:]
    queue = queue or {}
    totals = queue.get("totals", {})
    queued = totals.get("by_bucket", {})
    done = tally()
    by_bucket = {b: {"queued": queued.get(b, {}).get("count", 0), "queued_dollars": queued.get(b, {}).get("dollars", 0.0),
                     **done.get(b, {})} for b in ("agent", "writeoff", "escalate")}
    minutes = queue.get("policy", {}).get("minutes_per_touch", 6)
    summary = {
        "updated_at": now(),
        "as_of": queue.get("as_of", previous.get("as_of")),
        "snapshot_id": snapshot_id(queue) if queue else previous.get("snapshot_id"),
        "model": runs[-1]["model"] if runs else model_config()[1],
        "queued": totals.get("queued", 0),
        "queued_dollars": totals.get("queued_dollars", 0.0),
        "drafted": sum(c["drafted"] for c in done.values()),
        "awaiting_approval": sum(c["awaiting_approval"] for c in done.values()),
        "approved": done["agent"]["approved"],
        "approved_dollars": done["agent"]["approved_dollars"],
        "written_off": done["writeoff"]["approved"],
        "written_off_dollars": done["writeoff"]["approved_dollars"],
        "rejected": sum(c["rejected"] for c in done.values()),
        "hours_queued": round(totals.get("queued", 0) * minutes / 60, 1),
        "by_bucket": by_bucket,
        "last_run": runs[-1] if runs else None,
        "runs": runs,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(SUMMARY, summary)
    return summary


def heartbeat(summary):
    last = summary.get("last_run") or {}
    return (
        f"Collections heartbeat, snapshot {summary['snapshot_id']}: {summary['queued']:,} queued "
        f"({money(summary['queued_dollars'])}), {summary['drafted']:,} drafted, "
        f"{summary['awaiting_approval']:,} awaiting approval, {summary['approved']:,} approved "
        f"({money(summary['approved_dollars'])}), {summary['written_off']:,} written off "
        f"({money(summary['written_off_dollars'])}). Model {summary['model']}, last run {last.get('started_at', 'never')}."
    )
