"""Answer a collector's question from the agent's own files, and say exactly which lookups produced the answer.

Every answer is built from deterministic lookups (the export, the queue, the drafts and their approval state, the
stale-draft check, disputes and blocker cases). The local model only rephrases a free-form question when no lookup
matches; it never invents a fact the lookups did not return.
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "triage"))
import triage  # noqa: E402

OUT = ROOT / "out"
INVOICE = re.compile(r"\bINV-?\d{5,}\b", re.I)


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, (dict, list)) else None


def money(x):
    return f"${float(x):,.2f}"


def count(n, one, many=None):
    n = int(n or 0)
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


class World:
    """The agent's state on disk, reloaded when the snapshot changes."""

    def __init__(self):
        self.key = None
        self.load()

    def fingerprint(self):
        parts = []
        for p in (OUT / "queue.json", OUT / "summary.json", OUT / "revalidation.json", OUT / "disputes" / "summary.json"):
            try:
                parts.append(p.stat().st_mtime_ns)
            except OSError:
                parts.append(0)
        try:
            parts.append(max((p.stat().st_mtime_ns for p in (OUT / "actions").glob("*/status.json")), default=0))
        except OSError:
            parts.append(0)
        return tuple(parts)

    def load(self):
        key = self.fingerprint()
        if key == self.key:
            return
        self.key = key
        self.policy = triage.load_policy(ROOT / "policy.yaml")
        self.invoices, self.notes, self.slack, self.as_of = triage.load_sources(
            triage.pick_sources(ROOT / "data" / "inbox", ROOT / "data" / "exports"))
        self.queue = read_json(OUT / "queue.json") or {"items": [], "totals": {}, "packages": []}
        self.items = {i["invoice_id"]: i for i in self.queue.get("items", [])}
        self.order = [i["invoice_id"] for i in self.queue.get("items", []) if i["bucket"] == "agent"]
        self.summary = read_json(OUT / "summary.json") or {}
        self.disputes = read_json(OUT / "disputes" / "summary.json") or {}
        self.recheck = read_json(OUT / "revalidation.json") or {}
        self.touch = {}
        for n in self.notes:
            d = n.get("touch_date")
            if d and d > self.touch.get(n.get("invoice_id"), ("", ""))[0]:
                self.touch[n["invoice_id"]] = (d, f"collector sheet ({n.get('collector') or 'unknown'})")
        self.accounts = {}
        for iid, inv in self.invoices.items():
            name = (inv.get("account_name") or "").lower()
            if name:
                self.accounts.setdefault(name, inv.get("account_id"))

    def action(self, iid):
        folder = OUT / "actions" / iid
        return {
            "status": read_json(folder / "status.json"),
            "recheck": read_json(folder / "revalidation.json"),
            "case": read_json(folder / "salesforce_case.json"),
        } if folder.is_dir() else None

    def dispute(self, iid):
        return read_json(OUT / "disputes" / f"{iid}.json")

    def blocker(self, iid):
        return read_json(OUT / "disputes" / "cases" / f"{iid}.json")


def latest_touch(w, iid, inv):
    touches = []
    if inv.get("last_touch_date"):
        touches.append((inv["last_touch_date"], "Salesforce"))
    if iid in w.touch:
        touches.append(w.touch[iid])
    thread = w.slack.get(iid)
    if thread and thread.get("last_message_date"):
        touches.append((thread["last_message_date"], f"Slack {thread.get('channel') or ''}".strip()))
    return max(touches) if touches else (None, None)


def why_not_queued(w, iid, inv):
    p = w.policy
    status = (inv.get("status") or "").lower()
    if status == "paid":
        return "it's paid"
    balance = float(inv["amount"]) - float(inv.get("amount_paid") or 0)
    dpd = (w.as_of - date.fromisoformat(inv["due_date"])).days
    when, where = latest_touch(w, iid, inv)
    if dpd < p["min_days_past_due"]:
        return f"it's only {dpd} days past due; the agent starts at {p['min_days_past_due']}"
    if balance > p["agent_max_amount"]:
        return f"the balance is {money(balance)}, over the {money(p['agent_max_amount'])} agent limit, so a collector owns it"
    if when and (w.as_of - date.fromisoformat(when)).days < p["stale_touch_days"]:
        return f"a person touched it on {when} via {where}, inside the {p['stale_touch_days']}-day window"
    if balance <= 0:
        return "it shows no balance left"
    return "it didn't meet the queue rules in the latest run"


def about_invoice(w, iid):
    steps, sources = [f"Looked up {iid} in the {w.as_of} export"], [f"data export as of {w.as_of}"]
    inv = w.invoices.get(iid)
    if not inv:
        return f"I can't find {iid} in the latest export.", steps, sources
    balance = float(inv["amount"]) - float(inv.get("amount_paid") or 0)
    head = f"{iid}, {inv.get('account_name')}: {money(balance)} open, status {inv.get('status')}."
    lines = [head]
    item = w.items.get(iid)
    steps.append("Checked the agent's queue (out/queue.json)")
    sources.append("out/queue.json")
    if not item:
        lines.append(f"It isn't in the agent's queue because {why_not_queued(w, iid, inv)}.")
        return " ".join(lines), steps, sources
    lines.append(f"Queued in the {item['bucket']} bucket: {item['reason']}, {item['days_past_due']} days past due.")
    if item.get("coordinate"):
        lines.append(item["coordinate"]["reason"])
    act = w.action(iid)
    steps.append(f"Read out/actions/{iid}/ (draft, approval state, stale-draft check)")
    if act and act["status"]:
        s = act["status"]
        sources.append(f"out/actions/{iid}/status.json")
        state = s.get("state")
        who = s.get("by") or "someone"
        at = (s.get("at") or "").replace("T", " ")
        lines.append({"drafted": f"The agent drafted it at {at}; it's waiting for approval.",
                      "edited": f"{who} edited the draft at {at}; it still needs approval.",
                      "approved": f"Approved by {who} at {at}.",
                      "rejected": f"Rejected by {who} at {at}."}.get(state, f"Draft state: {state}."))
        r = act["recheck"]
        if r:
            sources.append(f"out/actions/{iid}/revalidation.json")
            if r.get("verdict") == "withdraw":
                lines.append("Withdrawn by the stale-draft check: " + "; ".join(r.get("reasons") or []) + ". Don't send it.")
            else:
                lines.append(f"Still valid against the {r.get('checked_against')} export.")
    elif item["bucket"] == "agent":
        pos = w.order.index(iid) + 1 if iid in w.order else None
        lines.append(f"Not drafted yet. It's #{pos:,} of {len(w.order):,} in the agent's line (oldest first); "
                     f"the agent drafts a few per cycle." if pos else "Not drafted yet.")
    elif item["bucket"] == "writeoff":
        c = item.get("writeoff_controls") or {}
        lines.append("Proposed write-off. " + ("Needs a controller: " + "; ".join(c.get("reasons") or []) + "."
                                               if c.get("needs_controller") else "Within the write-off limits."))
    if item["bucket"] == "escalate":
        d = w.dispute(iid)
        steps.append(f"Read the dispute packet out/disputes/{iid}.json")
        if d:
            sources.append(f"out/disputes/{iid}.json")
            lines.append(f"Disputed ({d.get('reason_label')}), {d.get('priority')}, routed to "
                         f"{d.get('route_email')}, reply due {d.get('respond_by')}.")
        b = w.blocker(iid)
        if b:
            sources.append(f"out/disputes/cases/{iid}.json")
            last = (b.get("history") or [{}])[-1]
            lines.append(f"Blocker case: {b.get('label')}. Latest: {last.get('detail')}")
    return " ".join(lines), steps, sources


def about_account(w, name):
    aid = w.accounts[name]
    pkg = next((p for p in w.queue.get("packages", []) if p["account_id"] == aid), None)
    steps = [f"Matched the account name to {aid}", "Read its package in out/queue.json"]
    if not pkg:
        return f"{name.title()} ({aid}) has nothing in the agent's queue right now.", steps, ["out/queue.json"]
    text = (f"{pkg['account_name']} ({aid}), owner {pkg['account_owner']}: {pkg['count']} queued invoices, "
            f"{money(pkg['dollars'])}, oldest {pkg['oldest_days_past_due']} days past due; "
            f"{money(pkg['open_balance'])} open on the whole account.")
    if pkg.get("coordinate"):
        text += " Someone is working a large invoice on this account, so reminders should go through the owner."
    return text, steps, ["out/queue.json"]


def waiting(w):
    s = w.summary
    by = s.get("by_bucket", {})
    text = (f"{count(s.get('awaiting_approval', 0), 'draft')} waiting for approval, {s.get('approved', 0):,} approved "
            f"({money(s.get('approved_dollars', 0))}), {s.get('rejected', 0):,} rejected.")
    wo = by.get("writeoff", {}).get("awaiting_approval", 0)
    if wo:
        text += f" {count(wo, 'write-off')} among the waiting ones."
    r = w.recheck
    if r.get("withdrawn"):
        text += f" The stale-draft check withdrew {r['withdrawn']:,} ({r.get('withdrawn_approved', 0):,} already approved)."
    return text, ["Read the agent summary (out/summary.json)", "Read the stale-draft report (out/revalidation.json)"], \
        ["out/summary.json", "out/revalidation.json"]


def withdrawn(w):
    r = w.recheck
    items = r.get("items") or []
    if not items:
        return f"Nothing is withdrawn: {count(r.get('checked', 0), 'draft')} checked against {r.get('as_of')}, all still valid.", \
            ["Read out/revalidation.json"], ["out/revalidation.json"]
    lines = [f"{len(items):,} withdrawn on the {r.get('as_of')} check:"]
    lines += [f"{i['invoice_id']} ({i['state']}): {'; '.join(i['reasons'])}" for i in items[:6]]
    return " ".join(lines), ["Read out/revalidation.json"], ["out/revalidation.json"]


def blockers(w):
    b = (w.disputes.get("blocker_cases") or {})
    st = b.get("by_state") or {}
    text = (f"{count(b.get('open', 0), 'missing-PO case')} open: {st.get('ready', 0):,} ready to send with a checked PO, "
            f"{st.get('blocked', 0):,} blocked on a PO that doesn't match, "
            f"{st.get('requested', 0) + st.get('accepted', 0):,} waiting on the account owner.")
    return text, ["Read out/disputes/summary.json"], ["out/disputes/summary.json"]


def disputes(w):
    d = w.disputes
    if not d:
        return "No dispute run yet.", ["Looked for out/disputes/summary.json"], []
    p1 = (d.get("by_priority") or {}).get("P1", {}).get("count", 0)
    return (f"{d.get('disputes', 0):,} disputed small invoices, {money(d.get('dollars', 0))}. {p1:,} are P1; "
            f"{d.get('no_reason_recorded', 0):,} have no reason on file; {d.get('stale_flags', 0):,} look settled."), \
        ["Read out/disputes/summary.json"], ["out/disputes/summary.json"]


def overview(w):
    t = w.queue.get("totals", {})
    return (f"As of {w.queue.get('as_of')}: {t.get('queued', 0):,} small invoices queued ({money(t.get('queued_dollars', 0))}) "
            f"in {t.get('packages', 0):,} account packages. {t.get('coordinate_with_owner', 0):,} should go through "
            f"an owner who is already talking to the customer; {t.get('writeoff_needs_controller', 0):,} write-offs "
            f"need a controller."), ["Read out/queue.json totals"], ["out/queue.json"]


def controllers(w):
    rows = [i for i in w.queue.get("items", []) if (i.get("writeoff_controls") or {}).get("needs_controller")]
    if not rows:
        return "No write-off needs a controller.", ["Scanned write-offs in out/queue.json"], ["out/queue.json"]
    accts = {}
    for i in rows:
        accts.setdefault(i["account_name"], []).append(i)
    top = sorted(accts.items(), key=lambda kv: -len(kv[1]))[:4]
    return (f"{len(rows):,} write-offs need a controller across {len(accts):,} accounts. Largest: "
            + "; ".join(f"{n} ({len(v)} invoices, {money(sum(x['balance'] for x in v))})" for n, v in top) + "."), \
        ["Scanned write-off controls in out/queue.json"], ["out/queue.json"]


TOPICS = [
    (re.compile(r"withdr|stale|invalid|pulled"), withdrawn),
    (re.compile(r"\bpo\b|blocker|purchase order|blocked"), blockers),
    (re.compile(r"controller|write.?off"), controllers),
    (re.compile(r"wait|pending|approv|review|accepted"), waiting),
    (re.compile(r"disput|p1"), disputes),
    (re.compile(r"overview|summary|how much|how many|status|today|queue"), overview),
]


def answer(w, question):
    w.load()
    started = time.perf_counter()
    q = question.strip()
    ids = [m.upper().replace("INV", "INV-").replace("--", "-") for m in INVOICE.findall(q)]
    parts, steps, sources = [], [], []
    for iid in dict.fromkeys(ids):
        a, s, src = about_invoice(w, iid)
        parts.append(a); steps += s; sources += src
    if not parts:
        low = q.lower()
        name = next((n for n in sorted(w.accounts, key=len, reverse=True) if n and n in low), None)
        if name:
            a, s, src = about_account(w, name)
            parts.append(a); steps += s; sources += src
        else:
            for pattern, fn in TOPICS:
                if pattern.search(low):
                    a, s, src = fn(w)
                    parts.append(a); steps += s; sources += src
                    break
    if not parts:
        parts.append("I answer from the agent's own records. Ask about an invoice (INV-112347), an account name, "
                     "what's waiting for approval, what was withdrawn, missing-PO cases, disputes or write-offs.")
    return {"answer": " ".join(parts), "steps": steps, "sources": list(dict.fromkeys(sources)),
            "ms": round((time.perf_counter() - started) * 1000), "as_of": str(w.as_of)}
