"""The collector's workspace: one page with the work, and a sidebar to ask the agent about it.

python app/server.py                       # http://<box>:8000 (0.0.0.0, like the other dashboards)
python app/server.py --host 127.0.0.1

Reads the agent's files (export, queue, drafts, stale-draft check, disputes, blocker cases). Approve, reject and
"draft now" are forwarded to the approval dashboard (channel/dashboard.py on :8080), which owns those decisions.
Zero network beyond this machine.
"""
import argparse
import http.client
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ask  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
OUT = ROOT / "out"
APPROVALS = ("127.0.0.1", 8080)
WORLD = ask.World()
DRAFT_FILES = ("customer_email.md", "customer_slack.md", "internal_note.md", "writeoff_memo.md")


def overview():
    w = WORLD
    w.load()
    t = w.queue.get("totals", {})
    s = w.summary
    d = w.disputes
    r = w.recheck
    return {
        "as_of": str(w.as_of),
        "model": s.get("model"),
        "last_run": (s.get("last_run") or {}).get("started_at"),
        "queued": t.get("queued", 0),
        "queued_dollars": t.get("queued_dollars", 0),
        "invoices": t.get("invoices", 0),
        "packages": t.get("packages", 0),
        "by_bucket": t.get("by_bucket", {}),
        "coordinate": t.get("coordinate_with_owner", 0),
        "controller": t.get("writeoff_needs_controller", 0),
        "hours": round(t.get("queued", 0) * w.policy["minutes_per_touch"] / 60),
        "drafted": s.get("drafted", 0),
        "awaiting": s.get("awaiting_approval", 0),
        "approved": s.get("approved", 0),
        "approved_dollars": s.get("approved_dollars", 0),
        "withdrawn": r.get("withdrawn", 0),
        "disputes": d.get("disputes", 0),
        "p1": (d.get("by_priority") or {}).get("P1", {}).get("count", 0),
        "blockers": d.get("blocker_cases") or {},
    }


def drafts():
    rows = []
    for folder in sorted((OUT / "actions").glob("*")):
        status = ask.read_json(folder / "status.json")
        item = ask.read_json(folder / "queue_item.json")
        if not isinstance(status, dict) or not isinstance(item, dict):
            continue
        recheck = ask.read_json(folder / "revalidation.json") or {}
        rows.append({"invoice_id": item["invoice_id"], "account_name": item.get("account_name"),
                     "balance": item.get("balance"), "days_past_due": item.get("days_past_due"),
                     "bucket": item.get("bucket"), "state": status.get("state"), "by": status.get("by"),
                     "at": status.get("at"), "withdraw": recheck.get("verdict") == "withdraw",
                     "withdraw_reasons": recheck.get("reasons") or []})
    order = {"drafted": 0, "edited": 1, "approved": 2, "rejected": 3}
    rows.sort(key=lambda r: (not r["withdraw"], order.get(r["state"], 9), -(r["days_past_due"] or 0)))
    return rows


def packages(limit=200):
    WORLD.load()
    return WORLD.queue.get("packages", [])[:limit]


def cases():
    rows = []
    for path in sorted((OUT / "disputes" / "cases").glob("*.json")):
        c = ask.read_json(path)
        if isinstance(c, dict) and c.get("state") != "closed":
            rows.append({k: c.get(k) for k in ("invoice_id", "account_name", "balance", "state", "label",
                                                "open_questions")} | {"latest": (c.get("history") or [{}])[-1]})
    order = {"ready": 0, "blocked": 1, "accepted": 2, "requested": 3}
    rows.sort(key=lambda r: (order.get(r["state"], 9), -(r["balance"] or 0)))
    return rows


def package(pid):
    WORLD.load()
    pkg = next((p for p in WORLD.queue.get("packages", []) if p["package_id"] == pid), None)
    if not pkg:
        return None
    items = [WORLD.items[i] for i in pkg["invoice_ids"] if i in WORLD.items]
    return {**pkg, "items": [{k: i.get(k) for k in ("invoice_id", "bucket", "balance", "days_past_due", "reason",
                                                    "last_touch_date", "last_touch_source")} for i in items],
            "context": items[0].get("account_context") if items else None,
            "coordinate_reason": next((i["coordinate"]["reason"] for i in items if i.get("coordinate")), None)}


def dispute_list(limit=300):
    rows = []
    for path in (OUT / "disputes").glob("INV-*.json"):
        d = ask.read_json(path)
        if isinstance(d, dict) and d.get("generated_by") == "disputes":
            rows.append({k: d.get(k) for k in ("invoice_id", "account_name", "balance", "priority", "reason_label",
                                                "respond_by", "route_to", "concede_recommended")})
    order = {"P1": 0, "P2": 1, "P3": 2}
    rows.sort(key=lambda r: (order.get(r["priority"], 9), -(r["balance"] or 0)))
    return rows[:limit]


def writeoffs():
    WORLD.load()
    rows = [i for i in WORLD.queue.get("items", []) if i["bucket"] == "writeoff"]
    rows.sort(key=lambda i: (not (i.get("writeoff_controls") or {}).get("needs_controller"), i["account_name"]))
    return [{"invoice_id": i["invoice_id"], "account_name": i["account_name"], "balance": i["balance"],
             "days_past_due": i["days_past_due"], "tiny_origin": i.get("tiny_origin"),
             "controls": i.get("writeoff_controls")} for i in rows]


def invoice(iid):
    w = WORLD
    w.load()
    inv = w.invoices.get(iid)
    if not inv:
        return None
    folder = OUT / "actions" / iid
    texts = {}
    for name in DRAFT_FILES:
        try:
            texts[name.removesuffix(".md")] = (folder / name).read_text(encoding="utf-8")
        except OSError:
            pass
    item = w.items.get(iid) or {}
    return {
        "invoice_id": iid,
        "account_name": inv.get("account_name"),
        "account_owner": inv.get("account_owner"),
        "balance": round(float(inv["amount"]) - float(inv.get("amount_paid") or 0), 2),
        "status": inv.get("status"),
        "due_date": inv.get("due_date"),
        "queue": {k: item.get(k) for k in ("bucket", "reason", "days_past_due", "last_touch_date",
                                            "last_touch_source", "coordinate", "account_context",
                                            "writeoff_controls")} if item else None,
        "action": w.action(iid),
        "drafts": texts,
        "dispute": w.dispute(iid),
        "case": w.blocker(iid),
        "explain": ask.about_invoice(w, iid)[0],
    }


def decide(iid, action, by):
    """Forward a decision to the approval dashboard, which owns approvals.

    Its success path for approve/reject is a redirect with no message; any ?msg= there is an error (for example
    "has no drafts yet"). Drafting always redirects with a message, so success is read from the draft on disk.
    """
    before = ask.read_json(OUT / "actions" / iid / "status.json")
    body = urlencode({"by": by or "collector"})
    conn = http.client.HTTPConnection(*APPROVALS, timeout=650)
    try:
        conn.request("POST", f"/invoice/{quote(iid)}/{action}", body=body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        location = resp.getheader("Location") or ""
    except OSError as e:
        return {"ok": False, "message": f"The approval dashboard on :{APPROVALS[1]} didn't answer ({e}). Start "
                                        f"channel/dashboard.py and try again."}
    finally:
        conn.close()
    msg = unquote(location.split("msg=", 1)[1].split("&", 1)[0]) if "msg=" in location else ""
    redirected = 300 <= resp.status < 400
    if action == "draft":
        after = ask.read_json(OUT / "actions" / iid / "status.json")
        ok = redirected and isinstance(after, dict) and after != before
        return {"ok": ok, "message": msg or ("Drafted." if ok else "Drafting didn't produce a draft.")}
    ok = redirected and not msg
    return {"ok": ok, "message": msg or {"approve": "Approved.", "reject": "Rejected."}[action]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                                                    "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/overview":
                return self.send(200, overview())
            if path == "/api/drafts":
                return self.send(200, drafts())
            if path == "/api/packages":
                return self.send(200, packages())
            if path == "/api/cases":
                return self.send(200, cases())
            if path == "/api/disputes":
                return self.send(200, dispute_list())
            if path == "/api/writeoffs":
                return self.send(200, writeoffs())
            if path.startswith("/api/package/"):
                rec = package(unquote(path.rsplit("/", 1)[-1]))
                return self.send(200, rec) if rec else self.send(404, {"error": "That account isn't in the queue."})
            if path.startswith("/api/invoice/"):
                rec = invoice(unquote(path.rsplit("/", 1)[-1]).upper())
                return self.send(200, rec) if rec else self.send(404, {"error": "That invoice isn't in the latest export."})
        except Exception as e:  # one bad file must not drop the connection
            return self.send(500, {"error": f"Couldn't read the agent's files: {type(e).__name__}: {e}"})
        self.send(404, {"error": "not found"})

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == (self.headers.get("Host") or "")

    def do_POST(self):
        path = urlsplit(self.path).path
        # JSON-only plus same-origin: a page elsewhere can't send a "simple" cross-site POST that approves an invoice.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.send(415, {"error": "send application/json"})
        if not self.same_origin():
            return self.send(403, {"error": "cross-origin requests are refused"})
        try:
            length = max(0, min(int(self.headers.get("Content-Length") or 0), 65536))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad JSON"})
        if not isinstance(body, dict):
            return self.send(400, {"error": "send a JSON object"})
        try:
            if path == "/api/ask":
                return self.send(200, ask.answer(WORLD, str(body.get("q") or "")[:500]))
            if path == "/api/decide":
                iid, action = str(body.get("invoice_id") or "").upper(), body.get("action")
                if action not in ("approve", "reject", "draft") or not ask.INVOICE.fullmatch(iid):
                    return self.send(400, {"error": "need an invoice id and approve, reject or draft"})
                return self.send(200, decide(iid, action, str(body.get("by") or "")[:60]))
        except Exception as e:
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})
        self.send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--approvals-port", type=int, default=8080)
    args = ap.parse_args()
    global APPROVALS
    APPROVALS = ("127.0.0.1", args.approvals_port)
    print(f"collector workspace on http://{args.host}:{args.port} (approvals via :{args.approvals_port})")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
