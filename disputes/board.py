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
/* Greenbar: a ledger that does its own follow-up. Summary band on paper, greenbar list left, one raised detail panel right. */
:root {
  --paper:#F4F6F2; --surface:#FFFFFF; --band:#E7EFE3; --line:#D3DACF; --ink:#172033; --ink-muted:#566070;
  --ledger:#2A4D8F; --on-ledger:#FFFFFF; --recovered:#1E6B47; --recovered-soft:#DDEDE3;
  --writeoff:#8A5A12; --writeoff-soft:#F6EBD7; --dispute:#A8402C; --dispute-soft:#F5E1DB;
  --sans:"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --r-sm:3px; --r-md:6px; --r-lg:10px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:#0F1520; --surface:#161E2B; --band:#1D2E29; --line:#2A3647; --ink:#E8ECEF; --ink-muted:#9BA6B4;
    --ledger:#8FB2EE; --on-ledger:#0F1520; --recovered:#6CCB98; --recovered-soft:#15301F;
    --writeoff:#E0A84E; --writeoff-soft:#33260F; --dispute:#E58A74; --dispute-soft:#3A1C16; color-scheme:dark;
  }
}
:root[data-theme="dark"] {
  --paper:#0F1520; --surface:#161E2B; --band:#1D2E29; --line:#2A3647; --ink:#E8ECEF; --ink-muted:#9BA6B4;
  --ledger:#8FB2EE; --on-ledger:#0F1520; --recovered:#6CCB98; --recovered-soft:#15301F;
  --writeoff:#E0A84E; --writeoff-soft:#33260F; --dispute:#E58A74; --dispute-soft:#3A1C16; color-scheme:dark;
}
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; background:var(--paper); color:var(--ink); font:14px/21px var(--sans); display:flex; flex-direction:column; }
.num { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.label { font-size:11px; line-height:14px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-muted); }

header { display:flex; flex-wrap:wrap; align-items:flex-end; gap:24px 48px; padding:24px 40px 20px; border-bottom:1px solid var(--line); }
.brand { flex:1 1 280px; min-width:0; }
.brand h1 { margin:0 0 6px; font-size:13px; line-height:16px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--ledger); }
.hero { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
.hero .big { font-size:56px; line-height:60px; font-weight:600; letter-spacing:-.02em; }
.hero .of { color:var(--ink-muted); }
.hero .of b { color:var(--ink); font-weight:600; }
.figs { display:flex; flex-wrap:wrap; gap:16px 36px; }
.fig .n { font-size:22px; line-height:28px; font-weight:500; }
.fig .n.alert { color:var(--dispute); } .fig .n.ok { color:var(--recovered); }

main { flex:1; min-height:0; display:grid; grid-template-columns:minmax(360px, 460px) 1fr; }
.list { display:flex; flex-direction:column; min-height:0; border-right:1px solid var(--line); }
.filters { display:flex; flex-wrap:wrap; gap:8px; padding:12px 16px; border-bottom:1px solid var(--line); }
select { font:inherit; font-size:13px; color:var(--ink); background:var(--surface); border:1px solid var(--line); border-radius:var(--r-md); padding:5px 8px; }
select:focus-visible, .row:focus-visible { outline:2px solid var(--ledger); outline-offset:2px; }
.count { padding:8px 16px; border-bottom:1px solid var(--line); }
#rows { overflow:auto; flex:1; }
.row { display:grid; grid-template-columns:44px 1fr auto; gap:0 12px; align-items:center; padding:10px 16px; cursor:pointer; background:var(--surface); border:0; width:100%; text-align:left; font:inherit; color:inherit; }
.row:nth-child(even) { background:var(--band); }
.row.sel { box-shadow:inset 3px 0 0 var(--ledger); }
.row .acct { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.row .meta { grid-column:2 / 4; color:var(--ink-muted); font-size:12px; line-height:18px; }
.row .amt { text-align:right; font-weight:500; }

.stamp { display:inline-block; font-size:11px; line-height:14px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; padding:2px 6px; border-radius:var(--r-sm); white-space:nowrap; }
.P1 { color:var(--dispute); background:var(--dispute-soft); }
.P2 { color:var(--writeoff); background:var(--writeoff-soft); }
.P3 { color:var(--ink-muted); background:var(--band); }
.concede { color:var(--recovered); background:var(--recovered-soft); }

.detail { overflow:auto; padding:24px 40px 64px; min-width:0; }
.panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--r-lg); padding:24px; max-width:860px; }
.empty { color:var(--ink-muted); padding:40px; }
.head { display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 16px; }
.head h2 { margin:0; font-size:28px; line-height:34px; font-weight:600; text-wrap:balance; }
.facts { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:16px 24px; margin:20px 0 24px; padding:16px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.facts .v { margin-top:4px; }
section.part { margin-top:20px; }
section.part > .label { display:block; margin-bottom:8px; }
.rec { font-size:15px; line-height:23px; }
.ev { margin:0; padding:0; list-style:none; }
.ev li { padding:8px 12px; background:var(--band); border-radius:var(--r-sm); margin-bottom:4px; }
.ev .src { display:block; color:var(--ink-muted); font-size:12px; }
.letter { border:1px solid var(--line); border-radius:var(--r-md); padding:14px 16px; }
.letter .to { color:var(--ink-muted); font-size:12px; }
.letter .subj { font-weight:600; margin:4px 0 8px; }
.letter pre { margin:0; white-space:pre-wrap; font:inherit; }
.case .subj { font-family:var(--mono); font-size:13px; }

@media (max-width:820px) {
  header, .detail { padding-left:16px; padding-right:16px; }
  main { grid-template-columns:1fr; }
  .list { max-height:50vh; border-right:0; border-bottom:1px solid var(--line); }
  .hero .big { font-size:40px; line-height:44px; }
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>Dispute desk</h1>
    <div class="hero" id="hero"><span class="big num">&nbsp;</span></div>
    <div class="label" id="asof" style="margin-top:6px">Worked on the box. Nothing here is sent anywhere.</div>
  </div>
  <div class="figs" id="figs"></div>
</header>
<main>
  <section class="list" aria-label="Disputes">
    <div class="filters">
      <select id="fp" aria-label="Priority"><option value="">All priorities</option><option>P1</option><option>P2</option><option>P3</option></select>
      <select id="fr" aria-label="Reason"><option value="">All reasons</option></select>
      <select id="ft" aria-label="Owner"><option value="">All owners</option><option value="account_owner">Account owner</option><option value="billing_ops">Billing ops</option><option value="deal_desk">Deal desk</option></select>
      <select id="fc" aria-label="Action"><option value="">Any action</option><option value="1">Concede</option><option value="0">Work it</option></select>
    </div>
    <div class="count label" id="count"></div>
    <div id="rows"></div>
  </section>
  <section class="detail" id="detail"><p class="empty">Loading disputes.</p></section>
</main>
<script>
const $ = s => document.querySelector(s);
const money = n => "$" + Number(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const TEAMS = {account_owner: "Account owner", billing_ops: "Billing ops", deal_desk: "Deal desk"};
const team = k => TEAMS[k] || String(k ?? "unknown");
const day = iso => { const d = new Date(iso + "T12:00:00"); return isNaN(d) ? esc(iso) : d.toLocaleDateString("en-US", {month: "short", day: "numeric"}); };
let all = [], current = null;

async function get(p) {
  const r = await fetch(p);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

function header(s) {
  if (!s.disputes) { $("#hero").innerHTML = '<span class="of">Run python disputes/disputes.py to fill this desk.</span>'; return; }
  $("#hero").innerHTML = `<span class="big num">${s.disputes.toLocaleString()}</span>
    <span class="of">disputed small invoices, <b class="num">${money(s.dollars)}</b></span>`;
  $("#asof").textContent = `Snapshot ${s.as_of}. Worked on the box. Nothing here is sent anywhere.`;
  const p1 = (s.by_priority.P1 || {count: 0}).count;
  $("#figs").innerHTML = [
    [s.no_reason_recorded, "no reason recorded", ""],
    [p1, "P1, reply in 2 days", "alert"],
    [s.stale_flags, "stale flags", ""],
    [s.concede_recommended.count, "concede, " + money(s.concede_recommended.dollars), "ok"],
  ].map(([n, l, c]) => `<div class="fig"><div class="n num ${c}">${Number(n).toLocaleString()}</div><div class="label">${esc(l)}</div></div>`).join("");
}

function shown() {
  const p = $("#fp").value, r = $("#fr").value, t = $("#ft").value, c = $("#fc").value;
  return all.filter(d => (!p || d.priority === p) && (!r || d.reason === r) && (!t || d.route_to === t)
                      && (!c || String(+d.concede_recommended) === c));
}

function render() {
  const list = shown();
  const focused = document.activeElement && document.activeElement.classList.contains("row") ? document.activeElement.dataset.id : null;
  $("#count").textContent = `${list.length.toLocaleString()} of ${all.length.toLocaleString()} disputes, highest priority first`;
  $("#rows").innerHTML = list.map(d => `
    <button class="row ${d.invoice_id === current ? "sel" : ""}" data-id="${esc(d.invoice_id)}"${d.invoice_id === current ? ' aria-current="true"' : ""}>
      <span class="stamp ${esc(d.priority)}">${esc(d.priority)}</span>
      <span class="acct">${esc(d.account_name)}</span><span class="amt num">${money(d.balance)}</span>
      <span class="meta"><span class="num">${esc(d.invoice_id)}</span> &middot; ${esc(d.reason_label)} &middot; ${esc(team(d.route_to))} &middot; reply by ${day(d.respond_by)}${d.concede_recommended ? ' &middot; <span class="stamp concede">concede</span>' : ""}</span>
    </button>`).join("") || '<p class="empty">No disputes match these filters.</p>';
  if (focused) {
    const again = [...document.querySelectorAll("#rows .row")].find(r => r.dataset.id === focused);
    if (again) again.focus({preventScroll: true});
  }
}

function letter(title, m, cls) {
  return `<section class="part"><span class="label">${esc(title)}</span><div class="letter ${cls || ""}">
    <div class="to">To ${esc(m.to)}${m.cc ? ", cc " + esc(m.cc) : ""}</div>
    <div class="subj">${esc(m.subject)}</div><pre>${esc(m.body)}</pre></div></section>`;
}

async function show(id) {
  current = id; render();
  let d;
  try { d = await get("/api/disputes/" + encodeURIComponent(id)); }
  catch (e) { if (id === current) $("#detail").innerHTML = '<p class="empty">This dispute is no longer in the latest run.</p>'; return; }
  if (id !== current) return;
  const c = d.salesforce_case;
  const ev = d.evidence.length
    ? `<ul class="ev">${d.evidence.map(e => `<li>${esc(e.text)}<span class="src">${esc(e.date)}, ${esc(e.source)}</span></li>`).join("")}</ul>`
    : '<p class="empty" style="padding:0;margin:0">Nobody recorded a reason. The holding reply asks the customer.</p>';
  $("#detail").innerHTML = `<article class="panel">
    <div class="head"><h2>${esc(d.account_name)}</h2><span class="num" style="color:var(--ink-muted)">${esc(d.invoice_id)}</span>
      <span class="stamp ${esc(d.priority)}">${esc(d.priority)}</span>${d.concede_recommended ? '<span class="stamp concede">concede</span>' : ""}</div>
    <div class="facts">
      <div><div class="label">Balance</div><div class="v num">${money(d.balance)}</div></div>
      <div><div class="label">Past due</div><div class="v num">${esc(d.days_past_due)} days</div></div>
      <div><div class="label">Reply by</div><div class="v">${day(d.respond_by)}</div></div>
      <div><div class="label">Owner</div><div class="v">${esc(team(d.route_to))}</div></div>
      <div><div class="label">Reason</div><div class="v">${esc(d.reason_label)}</div></div>
      <div><div class="label">Same account</div><div class="v">${d.account_other_queued.count ? `<span class="num">${d.account_other_queued.count}</span> more queued, <span class="num">${money(d.account_other_queued.dollars)}</span>` : "nothing else queued"}</div></div>
    </div>
    <section class="part"><span class="label">Recommended</span><div class="rec">${esc(d.resolution)}</div></section>
    <section class="part"><span class="label">Evidence</span>${ev}</section>
    ${letter("Note to " + team(d.route_to).toLowerCase(), d.owner_note)}
    ${letter("Holding reply to the customer", d.customer_ack)}
    <section class="part"><span class="label">Salesforce case ${esc(c.case_id)}, ${esc(c.type)}, ${esc(c.priority)} priority</span>
      <div class="letter case"><div class="subj">${esc(c.subject)}</div><pre>${esc(c.description)}</pre></div></section>
  </article>`;
}

async function load() {
  let s, list;
  try { [s, list] = await Promise.all([get("/api/summary"), get("/api/disputes")]); }
  catch (e) { return; }  // try again on the next poll
  header(s);
  const order = {P1: 0, P2: 1, P3: 2};
  all = list.sort((a, b) => order[a.priority] - order[b.priority] || b.balance - a.balance);
  const reasons = [...new Map(all.map(d => [d.reason, d.reason_label])).entries()];
  const fr = $("#fr"), keep = fr.value;
  fr.innerHTML = '<option value="">All reasons</option>' + reasons.map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
  fr.value = keep;
  render();
  if (!current && all.length) show(all[0].invoice_id);
  if (!all.length) $("#detail").innerHTML = '<p class="empty">No disputes in the latest run.</p>';
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
