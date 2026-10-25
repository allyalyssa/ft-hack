"""Read-only page for the dispute packets. Zero network: stdlib server, one inline page.

python disputes/board.py                    # http://<box>:8090
python disputes/board.py --host 127.0.0.1   # this machine only

Reads out/disputes/ on every request, so rerunning disputes.py shows up on the next refresh.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DISPUTES = ROOT / "out" / "disputes"
LIST_FIELDS = ("invoice_id", "account_name", "balance", "days_past_due", "reason", "reason_label", "route_to",
               "priority", "respond_by", "concede_recommended")


def load(path):
    # disputes.py may be rewriting or deleting files while the page polls; treat any bad read as missing.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def packets():
    out = []
    for p in sorted(DISPUTES.glob("*.json")):
        if p.name == "summary.json":
            continue
        data = load(p)
        if data and data.get("generated_by") == "disputes":
            out.append(data)
    return out


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
                                                    "style-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        if path == "/api/summary":
            return self.send(200, load(DISPUTES / "summary.json") or {})
        if path == "/api/disputes":
            return self.send(200, [{k: p.get(k) for k in LIST_FIELDS} for p in packets()])
        if path.startswith("/api/disputes/"):
            iid = unquote(path.rsplit("/", 1)[-1])
            match = next((p for p in packets() if p["invoice_id"] == iid), None)
            return self.send(200, match) if match else self.send(404, {"error": "not found"})
        self.send(404, {"error": "not found"})


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dispute Desk</title>
<style>
:root { --bg:#f6f5f2; --panel:#fff; --ink:#1c1b19; --muted:#6b6860; --line:#e4e1da; --chip:#efede7; --sel:#f3eee4;
        --p1:#a23b2c; --p2:#9a5b00; --p3:#55708f; --ok:#3a6b35; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151513; --panel:#1e1d1b; --ink:#ecebe6; --muted:#a09d94; --line:#2f2e2a; --chip:#2a2926; --sel:#2b2720;
          --p1:#e07a6a; --p2:#e0a64a; --p3:#8fb0d6; --ok:#79b26f; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
header { padding:18px 24px 14px; border-bottom:1px solid var(--line); }
h1 { margin:0 0 2px; font-size:17px; font-weight:650; }
.sub { color:var(--muted); font-size:12px; }
.kpis { display:flex; flex-wrap:wrap; gap:28px; margin-top:14px; }
.kpi .n { font-size:20px; font-weight:650; font-variant-numeric:tabular-nums; }
.kpi .l { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
main { display:grid; grid-template-columns:minmax(340px, 440px) 1fr; height:calc(100vh - 128px); }
.list { border-right:1px solid var(--line); overflow:auto; }
.filters { position:sticky; top:0; background:var(--bg); padding:10px 12px; border-bottom:1px solid var(--line); display:flex; gap:6px; flex-wrap:wrap; }
select { font:inherit; font-size:12px; color:var(--ink); background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:4px 6px; }
.row { display:grid; grid-template-columns:auto 1fr auto; gap:2px 10px; padding:10px 14px; border-bottom:1px solid var(--line); cursor:pointer; }
.row:hover { background:var(--panel); } .row.sel { background:var(--sel); }
.prio { font-size:11px; font-weight:700; align-self:start; padding:1px 6px; border-radius:4px; background:var(--chip); }
.P1 { color:var(--p1); } .P2 { color:var(--p2); } .P3 { color:var(--p3); }
.acct { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.amt { text-align:right; font-weight:600; font-variant-numeric:tabular-nums; }
.meta { grid-column:2 / 4; color:var(--muted); font-size:12px; }
.detail { overflow:auto; padding:24px 32px 64px; }
.empty { color:var(--muted); padding:40px; }
.facts { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:12px 24px; margin:14px 0 20px; }
.facts span { display:block; font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:14px; }
.card h3 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-weight:600; }
.card .to { font-size:12px; color:var(--muted); margin-bottom:6px; }
.card pre { margin:0; white-space:pre-wrap; font:inherit; }
.rec { border-left:3px solid var(--ok); }
.ev { margin:0; padding-left:18px; } .ev li { margin:2px 0; } .ev .src { color:var(--muted); font-size:12px; }
.concede { color:var(--ok); font-weight:600; }
@media (max-width:760px) { main { grid-template-columns:1fr; height:auto; } .list { max-height:45vh; } }
</style>
</head>
<body>
<header>
  <h1>Dispute desk</h1>
  <div class="sub" id="sub">Disputed small invoices, worked on the box. Nothing here is sent anywhere.</div>
  <div class="kpis" id="kpis"></div>
</header>
<main>
  <section class="list">
    <div class="filters">
      <select id="fp"><option value="">All priorities</option><option>P1</option><option>P2</option><option>P3</option></select>
      <select id="fr"><option value="">All reasons</option></select>
      <select id="ft"><option value="">All owners</option><option value="account_owner">Account owner</option><option value="billing_ops">Billing ops</option><option value="deal_desk">Deal desk</option></select>
      <select id="fc"><option value="">Any action</option><option value="1">Concede</option><option value="0">Work it</option></select>
    </div>
    <div id="rows"></div>
  </section>
  <section class="detail" id="detail"><p class="empty">Pick a dispute.</p></section>
</main>
<script>
const $ = s => document.querySelector(s);
const money = n => "$" + Number(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const TEAMS = {account_owner: "Account owner", billing_ops: "Billing ops", deal_desk: "Deal desk"};
const team = k => TEAMS[k] || String(k ?? "unknown");
let all = [], current = null;

async function get(p) {
  const r = await fetch(p);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

function kpis(s) {
  if (!s.disputes) { $("#kpis").innerHTML = '<div class="sub">Run python disputes/disputes.py first.</div>'; return; }
  $("#sub").textContent = `Disputed small invoices as of ${s.as_of}, worked on the box. Nothing here is sent anywhere.`;
  $("#kpis").innerHTML = [
    [s.disputes.toLocaleString(), "disputes", money(s.dollars)],
    [s.no_reason_recorded.toLocaleString(), "no reason recorded", ""],
    [(s.by_priority.P1 || {count: 0}).count.toLocaleString(), "P1", "respond in 2 days"],
    [s.stale_flags.toLocaleString(), "stale flags", "payment since promised"],
    [s.concede_recommended.count.toLocaleString(), "concede", money(s.concede_recommended.dollars)],
  ].map(([n, l, x]) => `<div class="kpi"><div class="n">${n}</div><div class="l">${l}${x ? " &middot; " + x : ""}</div></div>`).join("");
}

function shown() {
  const p = $("#fp").value, r = $("#fr").value, t = $("#ft").value, c = $("#fc").value;
  return all.filter(d => (!p || d.priority === p) && (!r || d.reason === r) && (!t || d.route_to === t)
                      && (!c || String(+d.concede_recommended) === c));
}

function render() {
  $("#rows").innerHTML = shown().map(d => `
    <div class="row ${d.invoice_id === current ? "sel" : ""}" data-id="${esc(d.invoice_id)}">
      <span class="prio ${esc(d.priority)}">${esc(d.priority)}</span>
      <span class="acct">${esc(d.account_name)}</span><span class="amt">${money(d.balance)}</span>
      <span class="meta">${esc(d.invoice_id)} &middot; ${esc(d.reason_label)} &middot; ${esc(team(d.route_to))} &middot; by ${esc(d.respond_by)}${d.concede_recommended ? ' &middot; <span class="concede">concede</span>' : ""}</span>
    </div>`).join("") || '<p class="empty">Nothing matches.</p>';
}

function letter(title, m) {
  return `<div class="card"><h3>${title}</h3><div class="to">To ${esc(m.to)}${m.cc ? ", cc " + esc(m.cc) : ""}</div>
    <div style="font-weight:600;margin-bottom:6px">${esc(m.subject)}</div><pre>${esc(m.body)}</pre></div>`;
}

async function show(id) {
  current = id; render();
  let d;
  try { d = await get("/api/disputes/" + encodeURIComponent(id)); }
  catch (e) { if (id === current) $("#detail").innerHTML = '<p class="empty">This dispute is no longer in the latest run.</p>'; return; }
  if (id !== current) return;
  const c = d.salesforce_case;
  const ev = d.evidence.length ? `<ul class="ev">${d.evidence.map(e => `<li>${esc(e.text)} <span class="src">${esc(e.date)}, ${esc(e.source)}</span></li>`).join("")}</ul>`
                               : '<p class="sub" style="margin:0">Nothing recorded by anyone.</p>';
  $("#detail").innerHTML = `
    <h2 style="margin:0;font-size:20px">${esc(d.account_name)} <span class="sub">${esc(d.invoice_id)}</span></h2>
    <div class="facts">
      <div><span>Balance</span>${money(d.balance)}</div><div><span>Past due</span>${esc(d.days_past_due)} days</div>
      <div><span>Priority</span><b class="${esc(d.priority)}">${esc(d.priority)}</b>, respond by ${esc(d.respond_by)}</div>
      <div><span>Owner</span>${esc(team(d.route_to))}</div><div><span>Reason</span>${esc(d.reason_label)}</div>
      <div><span>Account</span>${d.account_other_queued.count ? d.account_other_queued.count + " other queued, " + money(d.account_other_queued.dollars) : "no other queued invoices"}</div>
    </div>
    <div class="card rec"><h3>Recommended</h3>${esc(d.resolution)}</div>
    <div class="card"><h3>Evidence</h3>${ev}</div>
    ${letter("Note to " + esc(team(d.route_to).toLowerCase()), d.owner_note)}
    ${letter("Holding reply to customer", d.customer_ack)}
    <div class="card"><h3>Salesforce case ${esc(c.case_id)} &middot; ${esc(c.type)} &middot; ${esc(c.priority)}</h3>
      <div style="font-weight:600;margin-bottom:6px">${esc(c.subject)}</div><pre>${esc(c.description)}</pre></div>`;
}

async function load() {
  let s, list;
  try { [s, list] = await Promise.all([get("/api/summary"), get("/api/disputes")]); }
  catch (e) { return; }  // try again on the next poll
  kpis(s);
  const order = {P1: 0, P2: 1, P3: 2};
  all = list.sort((a, b) => order[a.priority] - order[b.priority] || b.balance - a.balance);
  const reasons = [...new Map(all.map(d => [d.reason, d.reason_label])).entries()];
  const fr = $("#fr"), keep = fr.value;
  fr.innerHTML = '<option value="">All reasons</option>' + reasons.map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
  fr.value = keep;
  render();
}

document.addEventListener("change", e => { if (e.target.tagName === "SELECT") render(); });
document.addEventListener("click", e => { const r = e.target.closest(".row"); if (r) show(r.dataset.id); });
load();
setInterval(() => { if (!document.hidden) load(); }, 30000);
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 so the Mac on the cable can open it, like the approval dashboard")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()
    print(f"dispute desk on http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
