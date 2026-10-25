"""Approval dashboard: the queue by bucket, the drafted package per invoice, approve / edit / reject.

python channel/dashboard.py [--host 0.0.0.0] [--port 8080] [--queue out/queue.json]

Standard library only, no external assets, so it keeps working with the uplink pulled.
"""
import argparse
import html
import subprocess
import sys
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import store  # noqa: E402

esc = html.escape
QUEUE_PATH = store.QUEUE
ROW_LIMIT = 300
CONFLICT = ' <span class="warn" title="Sources disagree on the last touch">conflict</span>'
TABS = (
    ("awaiting", "Awaiting approval"),
    ("agent", "Agent works it"),
    ("writeoff", "Write off"),
    ("escalate", "With a person"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
)
STATE_LABEL = {"drafted": "Awaiting approval", "edited": "Edited, awaiting approval", "approved": "Approved",
               "rejected": "Rejected"}
FIELD_FILE = {"email_subject": "customer_email.md", "email_body": "customer_email.md", "slack_message": "customer_slack.md",
              "internal_note": "internal_note.md", "memo_rationale": "writeoff_memo.md",
              "case_summary": "salesforce_case.json", "next_action": "salesforce_case.json"}
_cache = {"mtime": None, "queue": None}

CSS = """
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 color:#1b2330;background:#f4f6f9}
a{color:#1f5fbf;text-decoration:none}a:hover{text-decoration:underline}
header{background:#0f1b2d;color:#dfe6f0;display:flex;align-items:center;gap:28px;padding:0 24px;height:52px}
header .brand{font-weight:650;font-size:15px;color:#fff;letter-spacing:.2px}
header nav a{color:#b9c6d8;margin-right:18px}header nav a.on,header nav a:hover{color:#fff;text-decoration:none}
header .meta{margin-left:auto;font-size:12px;color:#9fb0c6;display:flex;gap:16px;align-items:center}
.local{display:inline-flex;align-items:center;gap:6px;color:#bfe8cf;font-weight:600}
.local:before{content:"";width:8px;height:8px;border-radius:50%;background:#3ccf7a}
main{padding:22px 24px 60px;max-width:1500px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:20px}
.kpi{background:#fff;border:1px solid #e1e6ee;border-radius:8px;padding:12px 14px}
.kpi .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}
.kpi .l{font-size:12px;color:#5d6b7f}.kpi .s{font-size:12px;color:#8592a5}
.tabs{display:flex;gap:4px;border-bottom:1px solid #d9dfe8;margin-bottom:0}
.tabs a{padding:9px 14px;color:#4a586c;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a.on{color:#0f1b2d;border-color:#1f5fbf;font-weight:600}.tabs a:hover{text-decoration:none;color:#0f1b2d}
.tabs .n{background:#e8edf4;border-radius:10px;padding:1px 7px;font-size:11px;margin-left:6px;color:#4a586c}
.toolbar{display:flex;justify-content:space-between;align-items:center;padding:12px 0;color:#5d6b7f;font-size:13px}
.toolbar input{padding:6px 10px;border:1px solid #cdd5e0;border-radius:6px;width:280px;font:inherit}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e1e6ee;border-radius:8px;overflow:hidden}
th{text-align:left;font-size:12px;font-weight:600;color:#5d6b7f;background:#f8fafc;padding:8px 12px;border-bottom:1px solid #e1e6ee}
td{padding:8px 12px;border-bottom:1px solid #eef1f5;vertical-align:top}
tr.row{cursor:pointer}tr.row:hover td{background:#f5f8fd}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600;white-space:nowrap}
.p-none{background:#eef1f5;color:#5d6b7f}.p-drafted{background:#e3edfc;color:#1f4f9a}.p-edited{background:#efe6fb;color:#5b2f9a}
.p-approved{background:#dff5e7;color:#1d6b3c}.p-rejected{background:#f8e3e3;color:#9a2f2f}
.p-agent{background:#eef1f5;color:#34465e}.p-writeoff{background:#fdf0dc;color:#8a5a12}.p-escalate{background:#fde6e0;color:#97391f}
.warn{color:#9a5b00}
.flash{background:#fff8e1;border:1px solid #f0d98a;padding:10px 14px;border-radius:8px;margin-bottom:16px}
.title{display:flex;align-items:baseline;gap:14px;margin:4px 0 18px}
.title h1{font-size:22px;margin:0}.title .bal{font-size:22px;font-weight:650;margin-left:auto;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:start}
.card{background:#fff;border:1px solid #e1e6ee;border-radius:8px;padding:14px 16px;margin-bottom:14px}
.card h3{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#5d6b7f;margin:0 0 10px;display:flex;gap:8px;align-items:center}
dl{display:grid;grid-template-columns:120px 1fr;gap:5px 10px;margin:0;font-size:13px}dt{color:#5d6b7f}dd{margin:0;overflow-wrap:anywhere}
.note{font-size:13px;padding:6px 0;border-top:1px solid #eef1f5}.note:first-of-type{border-top:0}.note .who{color:#5d6b7f;font-size:12px}
.arts{display:grid;grid-template-columns:1fr 1fr;gap:14px}.arts .card{margin:0}.wide{grid-column:1/-1}
.body{white-space:pre-wrap;font-size:13.5px}
.subject{font-weight:600;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #eef1f5}
.bubble{background:#f3f5f8;border-radius:10px;padding:10px 12px;white-space:pre-wrap;font-size:13.5px}
.count{font-size:11px;color:#8592a5;margin-top:6px}
.tag{font-size:10px;font-weight:600;text-transform:none;letter-spacing:0;background:#fdf0dc;color:#8a5a12;padding:1px 6px;border-radius:8px}
pre.memo{white-space:pre-wrap;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;margin:0}
textarea{width:100%;min-height:180px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 border:1px solid #cdd5e0;border-radius:6px;padding:8px}
.actions{display:flex;gap:10px;align-items:center;background:#fff;border:1px solid #e1e6ee;border-radius:8px;
 padding:10px 14px;margin-bottom:14px;position:sticky;top:8px;z-index:2}
.actions input{padding:6px 9px;border:1px solid #cdd5e0;border-radius:6px;font:inherit;width:170px}
.actions .state{margin-left:auto;font-size:13px;color:#5d6b7f}
button,.btn{font:inherit;font-weight:600;border-radius:6px;padding:7px 16px;border:1px solid #cdd5e0;background:#fff;
 color:#1b2330;cursor:pointer;display:inline-block}
.btn:hover{text-decoration:none;background:#f5f8fd}
.approve{background:#1d7a45;border-color:#1d7a45;color:#fff}.approve:hover{background:#176639}
.reject{color:#9a2f2f}
.empty{background:#fff;border:1px dashed #cdd5e0;border-radius:8px;padding:28px;text-align:center;color:#5d6b7f}
"""


def queue():
    try:
        mtime = QUEUE_PATH.stat().st_mtime
    except FileNotFoundError:
        return None
    if _cache["mtime"] != mtime:
        try:
            _cache.update(queue=store.load_queue(QUEUE_PATH), mtime=mtime)
        except ValueError:
            pass
    return _cache["queue"]


def find_item(invoice_id):
    for item in (queue() or {}).get("items", []):
        if item["invoice_id"] == invoice_id:
            return item
    return store.read_json(store.action_dir(invoice_id) / "queue_item.json")


def rows():
    items = {i["invoice_id"]: i for i in (queue() or {}).get("items", [])}
    statuses = store.all_statuses()
    for invoice_id in statuses.keys() - items.keys():
        item = store.read_json(store.ACTIONS / invoice_id / "queue_item.json")
        if item:
            items[invoice_id] = item
    return [(item, statuses.get(iid)) for iid, item in items.items()]


def in_tab(tab, item, status):
    state = status["state"] if status else None
    if tab == "awaiting":
        return state in ("drafted", "edited")
    if tab in ("approved", "rejected"):
        return state == tab
    return item["bucket"] == tab


def state_pill(item, status):
    if status:
        return f'<span class="pill p-{status["state"]}">{STATE_LABEL[status["state"]]}</span>'
    if item["bucket"] == "escalate":
        return '<span class="pill p-escalate">With a person</span>'
    return '<span class="pill p-none">Not drafted</span>'


def bucket_pill(bucket):
    label = {"agent": "Agent", "writeoff": "Write off", "escalate": "Escalate"}[bucket]
    return f'<span class="pill p-{bucket}">{label}</span>'


def touch_text(item):
    if not item.get("last_touch_date"):
        return '<span class="warn">Never</span>'
    return f"{item['days_since_touch']}d ago <span style='color:#8592a5'>({esc(item['last_touch_source'])})</span>"


def page(title, body, active="queue", refresh=None):
    summary = store.read_json(store.SUMMARY, {})
    last = (summary.get("last_run") or {}).get("started_at", "never")
    model = (summary.get("last_run") or {}).get("model") or store.model_config()[1]
    snapshot = (queue() or {}).get("as_of", "none")
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
                  for key, href, label in (("queue", "/", "Queue"), ("crm", "/crm", "Salesforce cases")))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head>
<body><header><span class="brand">Small-Dollar Collections</span><nav>{nav}</nav>
<span class="meta"><span>Snapshot {esc(snapshot)}</span><span>Model {esc(model)}</span><span>Last run {esc(last.replace('T', ' '))}</span>
<span class="local">Running locally</span></span></header><main>{body}</main>
<script>document.querySelectorAll("tr[data-href]").forEach(r=>r.onclick=()=>location=r.dataset.href)</script>
</body></html>"""


def kpi(value, label, sub=""):
    return f'<div class="kpi"><div class="v">{value}</div><div class="l">{label}</div><div class="s">{sub}</div></div>'


def index(params):
    q = queue()
    all_rows = rows()
    counts = {key: sum(1 for item, status in all_rows if in_tab(key, item, status)) for key, _ in TABS}
    tab = params.get("tab", ["awaiting" if counts["awaiting"] else "agent"])[0]
    search = params.get("q", [""])[0].strip().lower()
    selected = [(i, s) for i, s in all_rows if in_tab(tab, i, s)]
    if search:
        selected = [(i, s) for i, s in selected
                    if search in f"{i['invoice_id']} {i['account_name']} {i['account_owner']}".lower()]
    if tab in ("awaiting", "approved", "rejected"):
        selected.sort(key=lambda r: r[1]["at"], reverse=True)

    totals = (q or {}).get("totals", {})
    by_bucket = totals.get("by_bucket", {})
    done = store.tally()
    minutes = (q or {}).get("policy", {}).get("minutes_per_touch", 6)
    kpis = "".join([
        kpi(f"{totals.get('queued', 0):,}", "Queued", f"{store.money(totals.get('queued_dollars', 0))} of {totals.get('invoices', 0):,} invoices"),
        kpi(f"{counts['awaiting']:,}", "Awaiting approval", f"{sum(c['drafted'] for c in done.values()):,} drafted so far"),
        kpi(f"{done['agent']['approved']:,}", "Approved to send", store.money(done["agent"]["approved_dollars"])),
        kpi(f"{done['writeoff']['approved']:,}", "Written off", f"{store.money(done['writeoff']['approved_dollars'])} of "
            f"{store.money(by_bucket.get('writeoff', {}).get('dollars', 0))} proposed"),
        kpi(f"{by_bucket.get('escalate', {}).get('count', 0):,}", "With a person", "disputed"),
        kpi(f"{totals.get('queued', 0) * minutes / 60:,.0f}", "Collector hours in queue", f"at {minutes:g} minutes a touch"),
    ])
    tabs = "".join(f'<a href="/?tab={key}" class="{"on" if key == tab else ""}">{label}<span class="n">{counts[key]:,}</span></a>'
                   for key, label in TABS)
    if q is None and not all_rows:
        return page("Collections queue", f'<div class="empty">No queue yet. Run <span class="mono">python3 triage/triage.py</span>.</div>')
    body_rows = "".join(
        f'<tr class="row" data-href="/invoice/{quote(i["invoice_id"])}"><td class="mono"><a href="/invoice/{quote(i["invoice_id"])}">'
        f'{esc(i["invoice_id"])}</a></td><td>{esc(i["account_name"])}</td><td>{esc(i["account_owner"])}</td>'
        f'<td class="num">{store.money(i["balance"], i.get("currency", "USD"))}</td><td class="num">{i["days_past_due"]}</td>'
        f'<td>{touch_text(i)}</td><td>{bucket_pill(i["bucket"])}</td><td>{state_pill(i, s)}'
        f'{CONFLICT if i.get("conflict") else ""}</td></tr>'
        for i, s in selected[:ROW_LIMIT])
    shown = f"Showing {min(len(selected), ROW_LIMIT):,} of {len(selected):,}"
    table = (f'<table><tr><th>Invoice</th><th>Account</th><th>Owner</th><th class="num">Balance</th><th class="num">Days past due</th>'
             f'<th>Last touch</th><th>Bucket</th><th>Status</th></tr>{body_rows}</table>') if selected else \
        '<div class="empty">Nothing here right now.</div>'
    body = f"""<div class="kpis">{kpis}</div><div class="tabs">{tabs}</div>
<div class="toolbar"><span>{shown}</span><form><input type="hidden" name="tab" value="{esc(tab)}">
<input name="q" value="{esc(search)}" placeholder="Search invoice, account or owner"></form></div>{table}"""
    return page("Collections queue", body, refresh=None if search else 30)


def facts_card(item):
    cur = item.get("currency", "USD")
    rows_ = [
        ("Account", f"{esc(item['account_name'])} <span style='color:#8592a5'>{esc(item['account_id'])}</span>"),
        ("Owner", f"{esc(item['account_owner'])}<br><span style='color:#8592a5'>{esc(item['owner_email'])}</span>"),
        ("Billing contact", esc(item["billing_contact_email"])),
        ("Invoice amount", store.money(item["amount"], cur)),
        ("Paid", store.money(item["amount_paid"], cur)),
        ("Balance", f"<b>{store.money(item['balance'], cur)}</b>"),
        ("Status", esc(item["status"].replace("_", " "))),
        ("Issued", store.long_date(item["issue_date"])),
        ("Due", store.long_date(item["due_date"])),
        ("Days past due", str(item["days_past_due"])),
        ("Last touch", touch_text(item)),
        ("Why queued", esc(item.get("reason", ""))),
    ]
    out = '<div class="card"><h3>Invoice</h3><dl>' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows_) + "</dl></div>"
    if item.get("conflict"):
        touches = "<br>".join(f"{esc(k)}: {esc(v)}" for k, v in item["conflict"]["touches"].items())
        out += (f'<div class="card"><h3 class="warn">Sources disagree</h3><div style="font-size:13px">{touches}<br>'
                f'{item["conflict"]["gap_days"]} days apart. Latest one is used.</div></div>')
    notes = sorted(item.get("sheet_notes") or [], key=lambda n: n["date"], reverse=True)
    if notes:
        out += '<div class="card"><h3>Collector sheet</h3>' + "".join(
            f'<div class="note"><div class="who">{esc(n["date"])} &middot; {esc(n["collector"] or "")}</div>{esc(n["note"] or "")}</div>'
            for n in notes) + "</div>"
    thread = item.get("slack_thread")
    if thread:
        out += f'<div class="card"><h3>Slack {esc(thread["channel"])}</h3>' + "".join(
            f'<div class="note"><div class="who">{esc(m["date"])} &middot; {esc(m["user"])}</div>{esc(m["text"])}</div>'
            for m in sorted(thread["messages"], key=lambda m: m["ts"])) + "</div>"
    return out


def artifact_cards(action, editing, template_files):
    files = action["files"]

    def tag(name):
        return '<span class="tag">template</span>' if name in template_files else ""

    def card(name, title, view, wide=False):
        if name not in files:
            return ""
        inner = f'<textarea name="{name}">{esc(files[name])}</textarea>' if editing else view
        return f'<div class="card{" wide" if wide else ""}"><h3>{title} {tag(name)}</h3>{inner}</div>'

    out = ""
    if "customer_email.md" in files:
        text = files["customer_email.md"]
        subject, _, body = text.partition("\n\n")
        out += card("customer_email.md", "Customer email",
                    f'<div class="subject">{esc(subject.removeprefix("Subject: "))}</div><div class="body">{esc(body.strip())}</div>')
    if "customer_slack.md" in files:
        text = files["customer_slack.md"].strip()
        out += card("customer_slack.md", "Customer Slack",
                    f'<div class="bubble">{esc(text)}</div><div class="count">{len(text)} / 600 characters</div>')
    out += card("writeoff_memo.md", "Write-off memo", f'<pre class="memo">{esc(files.get("writeoff_memo.md", ""))}</pre>', wide=True)
    out += card("internal_note.md", "Note to account owner", f'<div class="body">{esc(files.get("internal_note.md", "").strip())}</div>')
    case = action["case"]
    with closing(store.crm()) as db:
        row = db.execute("SELECT state, updated_by, updated_at FROM cases WHERE action_id = ?", (case.get("action_id"),)).fetchone()
    crm_state = f"{row['state']} by {row['updated_by']}" if row else "not in CRM"
    out += (f'<div class="card"><h3>Salesforce case {tag("salesforce_case.json")}</h3><dl>'
            f'<dt>Case</dt><dd class="mono">{esc(case.get("case_number", ""))}</dd>'
            f'<dt>Type</dt><dd>{esc(case.get("case_type", "").replace("_", " "))}</dd>'
            f'<dt>CRM status</dt><dd>{esc(crm_state)}</dd>'
            f'<dt>Summary</dt><dd>{esc(case.get("summary", ""))}</dd>'
            f'<dt>Next action</dt><dd>{esc(case.get("next_action", ""))}</dd>'
            f'<dt>Due</dt><dd>{esc(case.get("next_action_date", ""))}</dd>'
            f'<dt>Drafted by</dt><dd>{esc(case.get("model", ""))} at {esc(case.get("drafted_at", "").replace("T", " "))}</dd>'
            f'</dl></div>')
    return out


def dispute_cards(invoice_id, item):
    case = store.read_json(store.OUT / "disputes" / f"{invoice_id}.json")
    if case is None:
        return (f'<div class="empty">Disputed, so this one goes to a person. {esc(item["account_owner"])} owns the account.'
                f'<br>Run <span class="mono">python3 disputes/disputes.py</span> to work it up.</div>')
    evidence = "".join(f'<div class="note"><div class="who">{esc(e["date"])} &middot; {esc(e["source"])}</div>{esc(e["text"])}</div>'
                       for e in case["evidence"]) or '<div class="note">Nothing recorded.</div>'
    owner, ack = case["owner_note"], case["customer_ack"]
    return (f'<div class="arts"><div class="card wide"><h3>Dispute case {esc(case["case_id"])}</h3><dl>'
            f'<dt>Reason</dt><dd>{esc(case["reason_label"])}</dd>'
            f'<dt>Priority</dt><dd>{esc(case["priority"])}, respond by {esc(store.long_date(case["respond_by"]))}</dd>'
            f'<dt>Goes to</dt><dd>{esc(case["route_to"].replace("_", " "))} ({esc(case["route_email"] or "")})</dd>'
            f'<dt>Recommended</dt><dd>{esc(case["resolution"])}</dd></dl></div>'
            f'<div class="card wide"><h3>Evidence</h3>{evidence}</div>'
            f'<div class="card"><h3>Note to {esc(case["route_to"].replace("_", " "))}</h3>'
            f'<div class="subject">{esc(owner["subject"])}</div><div class="body">{esc(owner["body"])}</div></div>'
            f'<div class="card"><h3>Holding reply to customer</h3>'
            f'<div class="subject">{esc(ack["subject"])}</div><div class="body">{esc(ack["body"])}</div></div></div>')


def detail(invoice_id, params, reviewer):
    item = find_item(invoice_id)
    if item is None:
        return None
    action = store.load_action(invoice_id)
    status = action["status"] if action else None
    editing = bool(action) and params.get("edit") == ["1"]
    url = f"/invoice/{quote(invoice_id)}"
    flash = f'<div class="flash">{esc(params["msg"][0])}</div>' if params.get("msg") else ""
    title = (f'<div class="title"><h1 class="mono">{esc(invoice_id)}</h1><span>{esc(item["account_name"])}</span>'
             f'{bucket_pill(item["bucket"])}{state_pill(item, status)}'
             f'<span class="bal">{store.money(item["balance"], item.get("currency", "USD"))}</span></div>')
    who = f'<input name="by" value="{esc(reviewer)}" placeholder="Your name" required>'

    if action is None and item["bucket"] == "escalate":
        right = dispute_cards(invoice_id, item)
    elif action is None:
        model = store.model_config()[1]
        right = (f'<div class="empty"><p>No drafts yet. The scheduled run drafts a few invoices every cycle.</p>'
                 f'<form method="post" action="{url}/draft"><button class="approve">Draft now with {esc(model)}</button></form></div>')
    else:
        template_files = {FIELD_FILE[f] for f in action["case"].get("template_fields", [])}
        stamp = f'{"Drafted" if status["state"] == "drafted" else STATE_LABEL[status["state"]]} by {esc(status["by"])} at {esc(status["at"].replace("T", " "))}'
        if editing:
            bar = (f'<div class="actions">{who}<button class="approve" formaction="{url}/save">Save edits</button>'
                   f'<a class="btn" href="{url}">Cancel</a><span class="state">{stamp}</span></div>')
        else:
            bar = (f'<div class="actions">{who}<button class="approve" formaction="{url}/approve">Approve</button>'
                   f'<a class="btn" href="{url}?edit=1">Edit</a><button class="reject" formaction="{url}/reject">Reject</button>'
                   f'<span class="state">{stamp}</span></div>')
        right = f'<form method="post">{bar}<div class="arts">{artifact_cards(action, editing, template_files)}</div></form>'
    body = f'{flash}<div style="margin-bottom:8px"><a href="/">&larr; Queue</a></div>{title}<div class="grid"><div>{facts_card(item)}</div><div>{right}</div></div>'
    return page(f"{invoice_id} - Collections", body)


def crm_page():
    with closing(store.crm()) as db:
        cases = db.execute(
            "SELECT c.*, i.account_name, i.balance, i.currency, i.status AS invoice_status FROM cases c "
            "LEFT JOIN invoices i USING (invoice_id) ORDER BY c.updated_at DESC, c.case_id DESC LIMIT 500").fetchall()
        total = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    rows_ = "".join(
        f'<tr class="row" data-href="/invoice/{quote(c["invoice_id"])}"><td class="mono">CASE-{c["case_id"]:06d}</td>'
        f'<td class="mono"><a href="/invoice/{quote(c["invoice_id"])}">{esc(c["invoice_id"])}</a></td><td>{esc(c["account_name"] or "")}</td>'
        f'<td>{esc(c["case_type"].replace("_", " "))}</td><td class="num">{store.money(c["balance"] or 0, c["currency"] or "USD")}</td>'
        f'<td><span class="pill p-{esc(c["state"])}">{esc(c["state"])}</span></td><td>{esc(c["invoice_status"] or "")}</td>'
        f'<td style="max-width:420px">{esc(c["summary"] or "")}</td><td>{esc(c["next_action_date"] or "")}</td>'
        f'<td>{esc(c["updated_by"] or "")}<br><span style="color:#8592a5;font-size:12px">{esc((c["updated_at"] or "").replace("T", " "))}</span></td></tr>'
        for c in cases)
    table = (f'<table><tr><th>Case</th><th>Invoice</th><th>Account</th><th>Type</th><th class="num">Balance</th><th>Case status</th>'
             f'<th>Invoice status</th><th>Summary</th><th>Next action</th><th>Last change</th></tr>{rows_}</table>') if cases else \
        '<div class="empty">No cases yet. The agent opens one per drafted invoice.</div>'
    body = (f'<div class="toolbar"><span>Mock Salesforce: {total:,} cases in '
            f'<span class="mono">agent/crm_mock/crm.sqlite</span>, newest first</span></div>{table}')
    return page("Salesforce cases", body, active="crm", refresh=30)


class Handler(BaseHTTPRequestHandler):
    def reviewer(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["reviewer"].value if "reviewer" in cookie else ""

    def send_html(self, text, code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", f"reviewer={quote(cookie)}; Path=/; Max-Age=31536000")
        self.end_headers()

    def invoice_route(self, path):
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "invoice" and store.INVOICE_ID.match(parts[1]):
            return parts[1], (parts[2] if len(parts) > 2 else None)
        return None, None

    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if url.path == "/":
            return self.send_html(index(params))
        if url.path == "/crm":
            return self.send_html(crm_page())
        invoice_id, action = self.invoice_route(url.path)
        text = detail(invoice_id, params, self.reviewer()) if invoice_id and not action else None
        if text is None:
            return self.send_html(page("Not found", '<div class="empty">Not found. <a href="/">Back to the queue</a></div>'), 404)
        self.send_html(text)

    def do_POST(self):
        invoice_id, action = self.invoice_route(urlparse(self.path).path)
        if not invoice_id or action not in ("approve", "reject", "save", "draft"):
            return self.send_html(page("Not found", '<div class="empty">Not found.</div>'), 404)
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}
        by = form.get("by", "").strip() or self.reviewer() or "collector"
        url = f"/invoice/{quote(invoice_id)}"
        if action == "draft":
            proc = subprocess.run([sys.executable, str(ROOT / "agent" / "actions.py"), "--invoice", invoice_id, "--queue", str(QUEUE_PATH)],
                                  capture_output=True, text=True, timeout=600)
            lines = (proc.stdout + proc.stderr).strip().splitlines()
            msg = lines[0] if proc.returncode == 0 and lines else f"Drafting failed: {lines[-1] if lines else proc.returncode}"
            return self.redirect(f"{url}?msg={quote(msg)}")
        try:
            if action == "save":
                store.set_state(invoice_id, "edited", by, {k: v for k, v in form.items() if k in store.EDITABLE})
            else:
                store.set_state(invoice_id, "approved" if action == "approve" else "rejected", by)
        except LookupError as e:
            return self.redirect(f"{url}?msg={quote(str(e))}")
        store.build_summary(queue())
        self.redirect(url, cookie=by)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main():
    global QUEUE_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--queue", type=Path, default=store.QUEUE)
    args = ap.parse_args()
    QUEUE_PATH = args.queue.resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard on http://{args.host}:{args.port} reading {QUEUE_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
