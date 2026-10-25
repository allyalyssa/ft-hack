"""Answer questions about the invoices from fixed question stems. The local model only picks the stem and its values.

python agent/ask.py "has INV-116288 been collected?"
python agent/ask.py --stems                   # list what it can answer

Every stem is a fixed read-only query with an answer sentence, stored in the stems table of out/ask.sqlite. That
database is rebuilt from the exports, out/queue.json and out/disputes/ whenever they change; drafts and approvals are
read fresh on every question.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import actions
import store

sys.path.insert(0, str(store.ROOT / "triage"))
import triage  # noqa: E402

INVOICE_RE = re.compile(r"\bINV[-\s]?\d{3,}\b", re.I)
_build_lock = threading.Lock()

BLOCKER_OPTIONS = ", ".join(f'"{v}"' for v in store.BLOCKER_LABEL.values())
STEMS = [
    {
        "id": "invoice_status",
        "title": "Has an invoice been collected?",
        "examples": ["has INV-116288 been collected?", "is INV-100036 paid", "what's going on with INV-104225"],
        "params": ["invoice_id"],
        "sql": """
            SELECT CASE
                WHEN i.status = 'paid' THEN 'Yes. ' || i.invoice_id || ' (' || i.account_name || ') is paid in full, '
                     || money(i.amount) || '.'
                WHEN i.amount_paid > 0 THEN 'Partly. ' || i.account_name || ' has paid ' || money(i.amount_paid) || ' of '
                     || money(i.amount) || ' on ' || i.invoice_id || '; ' || money(i.balance) || ' is still open, '
                     || i.days_past_due || ' days past due.'
                ELSE 'No. ' || i.invoice_id || ' (' || i.account_name || ') has ' || money(i.balance) || ' open, '
                     || CASE WHEN i.days_past_due > 0 THEN i.days_past_due || ' days past due.' ELSE 'not due yet.' END
                END
                || CASE i.bucket WHEN 'agent' THEN ' It is in the agent queue'
                                 WHEN 'writeoff' THEN ' It is proposed for write-off'
                                 WHEN 'escalate' THEN ' It is disputed and with a person'
                                 ELSE '' END
                || CASE WHEN i.bucket IS NULL THEN ''
                        WHEN d.state IS NULL THEN ', not drafted yet.'
                        WHEN d.state IN ('drafted', 'edited') THEN ', with a draft awaiting approval.'
                        ELSE ', ' || d.state || ' by ' || d.by || '.' END
                || CASE WHEN i.blocker_label IS NOT NULL THEN ' Blocker: ' || i.blocker_label || '.' ELSE '' END
                || CASE WHEN i.latest_note IS NOT NULL THEN ' Latest note, ' || i.latest_note_date || ': "' || i.latest_note || '"'
                        ELSE '' END AS answer
            FROM invoices i LEFT JOIN drafts d USING (invoice_id) WHERE i.invoice_id = :invoice_id""",
        "none": "I can't find {invoice_id} in the exports.",
    },
    {
        "id": "invoice_notes",
        "title": "What has been said about an invoice?",
        "examples": ["any notes on INV-113701?", "what did the customer say about INV-104225", "history of INV-116288"],
        "params": ["invoice_id"],
        "sql": """SELECT date AS "Date", source AS "From", text AS "Note" FROM notes
                  WHERE invoice_id = :invoice_id ORDER BY date DESC LIMIT 10""",
        "many": "Latest activity on {invoice_id}, newest first.",
        "none": "Nobody has logged a note or Slack message on {invoice_id}.",
    },
    {
        "id": "account_summary",
        "title": "What does an account owe?",
        "examples": ["how much does Umbra Signal owe?", "what's the balance for Harbor Energy", "Neural Data total open"],
        "params": ["account"],
        "sql": """
            SELECT account_name || ' has ' || COUNT(*) || ' unpaid invoice' || CASE COUNT(*) WHEN 1 THEN '' ELSE 's' END
                   || ' totalling ' || money(SUM(balance)) || '. The oldest is ' || MAX(days_past_due)
                   || ' days past due, and ' || SUM(queued) || ' ' || CASE SUM(queued) WHEN 1 THEN 'is' ELSE 'are' END
                   || ' in the collections queue. Owner: ' || account_owner || '.' AS answer
            FROM invoices WHERE status != 'paid' AND account_name LIKE :account
            GROUP BY account_id ORDER BY SUM(balance) DESC LIMIT 3""",
        "none": "No unpaid invoices for an account matching \"{account}\".",
    },
    {
        "id": "account_invoices",
        "title": "List an account's unpaid invoices",
        "examples": ["list Umbra Signal's open invoices", "which invoices does Harbor Energy have open"],
        "params": ["account"],
        "sql": """SELECT invoice_id AS "Invoice", money(balance) AS "Balance", days_past_due AS "Days past due",
                         COALESCE(bucket, 'not queued') AS "Queue", COALESCE(blocker_label, '') AS "Blocker"
                  FROM invoices WHERE status != 'paid' AND account_name LIKE :account
                  ORDER BY invoices.days_past_due DESC LIMIT 25""",
        "many": "{n} unpaid invoices for accounts matching \"{account}\", oldest first.",
        "none": "No unpaid invoices for an account matching \"{account}\".",
    },
    {
        "id": "owner_queue",
        "title": "What is in an account owner's queue?",
        "examples": ["what's in Amara Nwosu's queue", "how many invoices does Sam have", "Elena's backlog"],
        "params": ["owner"],
        "sql": """
            SELECT account_owner || ' has ' || num(COUNT(*)) || ' invoices in the queue worth ' || money(SUM(balance)) || ': '
                   || SUM(bucket = 'agent') || ' for the agent, ' || SUM(bucket = 'writeoff') || ' proposed write-offs and '
                   || SUM(bucket = 'escalate') || ' disputed. ' || SUM(last_touch_date IS NULL) || ' have never been touched.'
                   AS answer
            FROM invoices WHERE queued AND account_owner LIKE :owner GROUP BY account_owner LIMIT 3""",
        "none": "No queued invoices for an owner matching \"{owner}\".",
    },
    {
        "id": "blocker_list",
        "title": "Which invoices are blocked, and why?",
        "examples": ["which customers say they already paid?", "who promised to pay", "invoices waiting on a PO",
                     "contacts that bounced"],
        "params": ["blocker"],
        "sql": """SELECT invoice_id AS "Invoice", account_name AS "Account", money(balance) AS "Balance",
                         latest_note_date AS "Noted", latest_note AS "Latest note", COUNT(*) OVER () AS total
                  FROM invoices WHERE queued AND blocker_label = :blocker ORDER BY invoices.balance DESC LIMIT 15""",
        "many": "{total} queued invoices are tagged \"{blocker}\". The largest:",
        "none": "No queued invoices are tagged \"{blocker}\".",
    },
    {
        "id": "biggest",
        "title": "Largest balances in the queue",
        "examples": ["biggest invoices in the queue", "top 10 balances", "which queued invoices are worth the most"],
        "params": [],
        "sql": """SELECT invoice_id AS "Invoice", account_name AS "Account", money(balance) AS "Balance",
                         days_past_due AS "Days past due", bucket AS "Queue"
                  FROM invoices WHERE queued ORDER BY invoices.balance DESC LIMIT 10""",
        "many": "The 10 largest balances in the queue:",
        "none": "The queue is empty.",
    },
    {
        "id": "oldest",
        "title": "Oldest past-due invoices in the queue",
        "examples": ["oldest invoices", "what's been past due the longest", "most overdue"],
        "params": [],
        "sql": """SELECT invoice_id AS "Invoice", account_name AS "Account", money(balance) AS "Balance",
                         days_past_due AS "Days past due", COALESCE(last_touch_date, 'never') AS "Last touch"
                  FROM invoices WHERE queued ORDER BY invoices.days_past_due DESC, invoices.balance DESC LIMIT 10""",
        "many": "The 10 most overdue invoices in the queue:",
        "none": "The queue is empty.",
    },
    {
        "id": "never_touched",
        "title": "Invoices nobody has ever touched",
        "examples": ["how many invoices were never touched", "untouched invoices", "what has nobody followed up on"],
        "params": [],
        "sql": """
            SELECT num(COUNT(*)) || ' queued invoices worth ' || money(SUM(balance))
                   || ' have never been touched by anyone in Salesforce, the collector sheet or Slack. '
                   || num(SUM(bucket = 'agent')) || ' of them are in the agent queue.' AS answer
            FROM invoices WHERE queued AND last_touch_date IS NULL""",
    },
    {
        "id": "awaiting_approval",
        "title": "Drafts waiting for approval",
        "examples": ["what needs my approval", "pending drafts", "how many drafts are waiting"],
        "params": [],
        "sql": """SELECT d.invoice_id AS "Invoice", i.account_name AS "Account", money(i.balance) AS "Balance",
                         replace(d.at, 'T', ' ') AS "Drafted", COUNT(*) OVER () AS total
                  FROM drafts d JOIN invoices i USING (invoice_id) WHERE d.state IN ('drafted', 'edited')
                  ORDER BY d.at DESC LIMIT 15""",
        "many": "{total} drafts are waiting for approval. Newest first:",
        "none": "Nothing is waiting for approval right now.",
    },
    {
        "id": "decisions",
        "title": "What has been approved, written off or rejected?",
        "examples": ["how much have we approved", "what was written off", "how many drafts were rejected"],
        "params": [],
        "sql": """
            SELECT COALESCE(SUM(d.state = 'approved' AND i.bucket = 'agent'), 0) || ' reminders approved ('
                   || money(COALESCE(SUM(CASE WHEN d.state = 'approved' AND i.bucket = 'agent' THEN i.balance END), 0))
                   || '), ' || COALESCE(SUM(d.state = 'approved' AND i.bucket = 'writeoff'), 0) || ' write-offs approved ('
                   || money(COALESCE(SUM(CASE WHEN d.state = 'approved' AND i.bucket = 'writeoff' THEN i.balance END), 0))
                   || '), ' || COALESCE(SUM(d.state = 'rejected'), 0) || ' drafts rejected and '
                   || COALESCE(SUM(d.state IN ('drafted', 'edited')), 0) || ' still waiting.' AS answer
            FROM drafts d JOIN invoices i USING (invoice_id)""",
    },
    {
        "id": "queue_totals",
        "title": "How big is the queue?",
        "examples": ["how big is the queue", "how many invoices are queued", "queue summary"],
        "params": [],
        "sql": """
            SELECT num(COUNT(*)) || ' invoices worth ' || money(SUM(balance)) || ' are queued: ' || num(SUM(bucket = 'agent'))
                   || ' for the agent (' || money(SUM(CASE WHEN bucket = 'agent' THEN balance END)) || '), '
                   || SUM(bucket = 'writeoff') || ' proposed write-offs (' || money(SUM(CASE WHEN bucket = 'writeoff' THEN balance END))
                   || ') and ' || SUM(bucket = 'escalate') || ' disputed (' || money(SUM(CASE WHEN bucket = 'escalate' THEN balance END))
                   || ').' AS answer
            FROM invoices WHERE queued""",
    },
    {
        "id": "collected",
        "title": "How much has been collected?",
        "examples": ["how much have we collected", "how many invoices are paid", "total paid"],
        "params": [],
        "sql": """
            SELECT num(SUM(status = 'paid')) || ' of ' || num(COUNT(*)) || ' invoices are paid in full ('
                   || money(SUM(CASE WHEN status = 'paid' THEN amount END)) || '). Another '
                   || num(SUM(status != 'paid' AND amount_paid > 0)) || ' are partly paid ('
                   || money(SUM(CASE WHEN status != 'paid' THEN amount_paid END)) || ' received so far).' AS answer
            FROM invoices""",
    },
    {
        "id": "repeat_accounts",
        "title": "Accounts with the most stale invoices",
        "examples": ["which accounts have the most queued invoices", "customers with lots of overdue invoices"],
        "params": [],
        "sql": """SELECT account_name AS "Account", COUNT(*) AS "Queued", money(SUM(balance)) AS "Total",
                         account_owner AS "Owner"
                  FROM invoices WHERE queued GROUP BY account_id ORDER BY COUNT(*) DESC, SUM(balance) DESC LIMIT 10""",
        "many": "Accounts with the most queued invoices. One message per account covers all of them:",
        "none": "The queue is empty.",
    },
    {
        "id": "disputes_by_reason",
        "title": "Why are invoices disputed?",
        "examples": ["why are invoices disputed", "dispute reasons", "how many disputes are P1"],
        "params": [],
        "sql": """SELECT reason_label AS "Reason", COUNT(*) AS "Disputes", money(SUM(balance)) AS "Balance",
                         SUM(priority = 'P1') AS "P1"
                  FROM disputes GROUP BY reason_label ORDER BY COUNT(*) DESC""",
        "many": "Disputed invoices by reason:",
        "none": "No dispute run yet.",
    },
]
STEM_BY_ID = {s["id"]: s for s in STEMS}


def db_path():
    return store.OUT / "ask.sqlite"


def build():
    """Rebuild the question database from the exports, the queue and the dispute packets."""
    folders = triage.pick_sources(store.ROOT / "data" / "inbox", store.ROOT / "data" / "exports")
    invoices, notes, slack, as_of = triage.load_sources(folders)
    queue = store.read_json(store.QUEUE, {"items": []})
    queued = {i["invoice_id"]: i for i in queue["items"]}
    path = db_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    with closing(sqlite3.connect(tmp)) as db, db:
        db.executescript("""
            CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY, account_id TEXT, account_name TEXT, account_owner TEXT,
                owner_email TEXT, billing_contact_email TEXT, currency TEXT, amount REAL, amount_paid REAL, balance REAL,
                status TEXT, issue_date TEXT, due_date TEXT, days_past_due INTEGER, last_touch_date TEXT, dispute_flag INTEGER,
                queued INTEGER, bucket TEXT, blocker_label TEXT, latest_note TEXT, latest_note_date TEXT);
            CREATE TABLE notes (invoice_id TEXT, date TEXT, source TEXT, text TEXT);
            CREATE INDEX notes_invoice ON notes (invoice_id);
            CREATE TABLE disputes (invoice_id TEXT, reason_label TEXT, priority TEXT, respond_by TEXT, route_to TEXT, balance REAL);
            CREATE TABLE stems (id TEXT PRIMARY KEY, title TEXT, examples TEXT, params TEXT, sql TEXT, many TEXT, none TEXT);
            CREATE INDEX invoices_account ON invoices (account_name);
            CREATE INDEX invoices_owner ON invoices (account_owner);
        """)
        rows = []
        for iid, inv in invoices.items():
            try:
                amount, paid = float(inv["amount"]), float(inv["amount_paid"] or 0)
                dpd = (as_of - triage.parse_date(inv["due_date"])).days
            except (KeyError, TypeError, ValueError):
                continue
            item = queued.get(iid)
            note = store.latest_note(item) if item else None
            key = store.blocker(item) if item else None
            rows.append((iid, inv["account_id"], inv["account_name"], inv["account_owner"], inv["owner_email"],
                         inv["billing_contact_email"], inv["currency"], amount, paid, round(amount - paid, 2),
                         inv["status"].strip().lower(), inv["issue_date"], inv["due_date"], dpd,
                         item["last_touch_date"] if item else (inv.get("last_touch_date") or None),
                         int(inv.get("dispute_flag", "").strip().lower() in ("true", "1", "yes", "y")),
                         int(bool(item)), item["bucket"] if item else None, store.BLOCKER_LABEL.get(key),
                         note[2] if note else None, note[0] if note else None))
        db.executemany(f"INSERT INTO invoices VALUES ({', '.join('?' * 21)})", rows)
        db.executemany("INSERT INTO notes VALUES (?, ?, ?, ?)",
                       [(n["invoice_id"], n.get("touch_date"), f"Sheet, {n.get('collector')}", n.get("note")) for n in notes]
                       + [(t["invoice_id"], m.get("date"), f"Slack {t.get('channel')}, {m.get('user')}", m.get("text"))
                          for t in slack.values() for m in t.get("messages", [])])
        packets = [store.read_json(p) for p in (store.OUT / "disputes").glob("INV-*.json")]
        db.executemany("INSERT INTO disputes VALUES (?, ?, ?, ?, ?, ?)",
                       [(p["invoice_id"], p["reason_label"], p["priority"], p["respond_by"], p["route_to"], p["balance"])
                        for p in packets if p])
        db.executemany("INSERT INTO stems VALUES (?, ?, ?, ?, ?, ?, ?)",
                       [(s["id"], s["title"], json.dumps(s["examples"]), json.dumps(s["params"]), s["sql"],
                         s.get("many"), s.get("none")) for s in STEMS])
    os.replace(tmp, path)


def sources_mtime():
    paths = [store.QUEUE, store.OUT / "disputes" / "summary.json", store.ROOT / "data" / "inbox"]
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0)


def ensure_db():
    with _build_lock:
        path = db_path()
        if not path.exists() or path.stat().st_mtime < sources_mtime():
            build()


def connect():
    db = sqlite3.connect(f"file:{db_path().as_posix()}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.create_function("money", 1, lambda x: store.money(x or 0), deterministic=True)
    db.create_function("num", 1, lambda x: f"{x or 0:,}", deterministic=True)
    db.execute("CREATE TEMP TABLE drafts (invoice_id TEXT PRIMARY KEY, state TEXT, by TEXT, at TEXT)")
    db.executemany("INSERT INTO temp.drafts VALUES (?, ?, ?, ?)",
                   [(iid, s["state"], s.get("by"), s.get("at")) for iid, s in store.all_statuses().items()])
    return db


def route(question, base_url, model):
    """Ask the model which stem fits and with what values. Returns (stem_id, params)."""
    menu = "\n".join(f"- {s['id']}({', '.join(s['params'])}): {s['title']} e.g. " + "; ".join(s["examples"])
                     for s in STEMS)
    system = (
        "Pick the one question stem that answers the user's question and fill in its values. Stems:\n" + menu +
        f"\n\nValues: invoice_id like INV-116288; account is a customer company name; owner is an account owner's "
        f"name; blocker is one of {BLOCKER_OPTIONS}. If nothing fits, use stem \"none\". "
        "Reply with JSON only: {\"stem\": \"...\", \"params\": {...}}"
    )
    body = {"model": model, "temperature": 0, "max_tokens": 80, "stream": False, "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        response = actions.post_json(url, body)
    except actions.urllib.error.HTTPError as e:
        if e.code not in (400, 422):
            raise
        del body["reasoning_effort"]
        response = actions.post_json(url, body)
    content = response["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{.*\}", re.sub(r"<think>.*?</think>", "", content, flags=re.S), re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    stem = re.match(r"\w*", str(data.get("stem") or "").strip()).group(0)
    return stem or "none", {k: str(v).strip() for k, v in params.items() if v is not None}


def clean_params(stem, params, question):
    values = {}
    for name in stem["params"]:
        value = params.get(name, "")
        if name == "invoice_id":
            found = INVOICE_RE.search(question) or INVOICE_RE.search(value)
            if not found:
                return None
            digits = re.sub(r"\D", "", found.group(0))
            values[name] = f"INV-{digits}"
        elif name == "blocker":
            label = next((v for v in store.BLOCKER_LABEL.values() if v.lower() == value.lower()), None)
            if label is None:
                return None
            values[name] = label
        else:
            if len(value) < 2:
                return None
            values[name] = value
    return values


def run_stem(stem, values):
    """Run one stem; returns (sentence, columns, rows)."""
    ensure_db()
    sql_params = {k: (f"%{v}%" if k in ("account", "owner") else v) for k, v in values.items()}
    with closing(connect()) as db:
        result = [dict(r) for r in db.execute(stem["sql"], sql_params).fetchmany(50)]
    if not result:
        return stem.get("none", "No matches.").format(**values), [], []
    if "answer" in result[0]:
        return " ".join(r["answer"] for r in result), [], []
    total = result[0].pop("total", None)
    for r in result[1:]:
        r.pop("total", None)
    sentence = stem["many"].format(n=len(result), total=f"{total:,}" if total else len(result), **values)
    columns = list(result[0])
    return sentence, columns, [[r[c] for c in columns] for r in result]


def help_text():
    return "I can answer these: " + "; ".join(s["title"].rstrip("?") for s in STEMS) + "."


def answer(question):
    started = time.perf_counter()
    question = question.strip()[:500]
    base_url, model = store.model_config()
    stem_id, params = route(question, base_url, model)
    stem = STEM_BY_ID.get(stem_id)
    values = clean_params(stem, params, question) if stem else None
    if stem is None or values is None:
        sentence, columns, rows, stem_id = help_text(), [], [], None
    else:
        sentence, columns, rows = run_stem(stem, values)
    return {"answer": sentence, "columns": columns, "rows": rows, "stem": stem["title"] if stem_id else None,
            "seconds": round(time.perf_counter() - started, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--stems", action="store_true")
    args = ap.parse_args()
    if args.stems or not args.question:
        print("\n".join(f"{s['id']}: {s['title']}  e.g. \"{s['examples'][0]}\"" for s in STEMS))
        return
    result = answer(args.question)
    print(result["answer"])
    if result["rows"]:
        print(" | ".join(result["columns"]))
        for r in result["rows"]:
            print(" | ".join(str(v) for v in r))
    print(f"[{result['stem'] or 'no stem'}, {result['seconds']}s]")


if __name__ == "__main__":
    main()
