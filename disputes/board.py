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
LIST_FIELDS = ("invoice_id", "account_name", "account_owner", "balance", "days_past_due", "reason", "reason_label", "route_to",
               "priority", "respond_by", "concede_recommended", "case")


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
/* Greenbar, Operate mode: a summary sentence and an aging strip on paper, a greenbar list left, one raised panel right. */
:root {
  --paper:#F4F6F2; --surface:#FFFFFF; --band:#E7EFE3; --line:#D3DACF; --ink:#172033; --ink-muted:#566070;
  --ledger:#2A4D8F; --ledger-soft:#E3EAF5; --on-ledger:#FFFFFF; --recovered:#1E6B47; --recovered-soft:#DDEDE3;
  --writeoff:#8A5A12; --writeoff-soft:#F6EBD7; --dispute:#A8402C; --dispute-soft:#F5E1DB;
  --age-1:#DCE6D6; --age-2:#EBDCB9; --age-3:#ECCBC1;
  --sans:system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  --mono:ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --r-sm:3px; --r-md:6px; --r-lg:10px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:#0F1520; --surface:#161E2B; --band:#1D2E29; --line:#2A3647; --ink:#E8ECEF; --ink-muted:#9BA6B4;
    --ledger:#8FB2EE; --ledger-soft:#1F2C44; --on-ledger:#0F1520; --recovered:#6CCB98; --recovered-soft:#15301F;
    --writeoff:#E0A84E; --writeoff-soft:#33260F; --dispute:#E58A74; --dispute-soft:#3A1C16;
    --age-1:#203430; --age-2:#3A2D14; --age-3:#46221A; color-scheme:dark;
  }
}
:root[data-theme="dark"] {
  --paper:#0F1520; --surface:#161E2B; --band:#1D2E29; --line:#2A3647; --ink:#E8ECEF; --ink-muted:#9BA6B4;
  --ledger:#8FB2EE; --ledger-soft:#1F2C44; --on-ledger:#0F1520; --recovered:#6CCB98; --recovered-soft:#15301F;
  --writeoff:#E0A84E; --writeoff-soft:#33260F; --dispute:#E58A74; --dispute-soft:#3A1C16;
  --age-1:#203430; --age-2:#3A2D14; --age-3:#46221A; color-scheme:dark;
}
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; background:var(--paper); color:var(--ink); font:14px/21px var(--sans); font-variant-numeric:tabular-nums;
       display:flex; flex-direction:column; accent-color:var(--ledger); caret-color:var(--ledger); scrollbar-color:var(--line) transparent; }
::selection { background:var(--ledger-soft); color:var(--ink); }
:focus-visible { outline:2px solid var(--ledger); outline-offset:2px; }
.mono { font-family:var(--mono); }
.muted { color:var(--ink-muted); }

header { padding:20px 40px 18px; border-bottom:1px solid var(--line); display:grid; grid-template-columns:minmax(0, 1fr) minmax(320px, 560px); gap:16px 48px; align-items:end; }
header h1 { margin:0 0 6px; font-size:22px; line-height:28px; font-weight:650; }
.lede { margin:0; max-width:62ch; color:var(--ink-muted); }
.lede b { color:var(--ink); font-weight:600; }
.lede .p1 { color:var(--dispute); font-weight:600; }
.status { margin-top:6px; font-size:12px; color:var(--ink-muted); }
.status.err { color:var(--dispute); }

.aging { min-width:0; }
.aging .cap { display:flex; justify-content:space-between; font-size:12px; color:var(--ink-muted); margin-bottom:6px; }
.strip { display:flex; gap:2px; height:44px; }
.seg { flex:1 1 0; min-width:72px; border:0; border-radius:var(--r-sm); padding:4px 8px; text-align:left; cursor:pointer; color:var(--ink); font:inherit; font-size:12px; line-height:17px; display:flex; flex-direction:column; justify-content:center; transition:filter 150ms ease-out; }
.seg:hover { filter:brightness(0.96); }
.seg[aria-pressed="true"] { outline:2px solid var(--ledger); outline-offset:1px; }
.seg b { font-size:13px; font-weight:600; }
.a1 { background:var(--age-1); } .a2 { background:var(--age-2); } .a3 { background:var(--age-3); }

main { flex:1; min-height:0; display:grid; grid-template-columns:minmax(360px, 460px) 1fr; }
.list { display:flex; flex-direction:column; min-height:0; border-right:1px solid var(--line); }
.tools { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:12px 16px; border-bottom:1px solid var(--line); }
.tools input { grid-column:1 / -1; }
input, select, .btn { font:inherit; font-size:13px; color:var(--ink); background:var(--surface); border:1px solid var(--line); border-radius:var(--r-md); padding:6px 8px; min-width:0; }
input::placeholder { color:var(--ink-muted); }
input:hover, select:hover { border-color:var(--ink-muted); }
.count { display:flex; justify-content:space-between; align-items:center; padding:8px 16px; border-bottom:1px solid var(--line); font-size:12px; color:var(--ink-muted); }
.linkbtn { border:0; background:none; color:var(--ledger); font:inherit; font-size:12px; cursor:pointer; padding:0; text-decoration:underline; text-underline-offset:3px; }
#rows { overflow:auto; flex:1; }
.row { display:grid; grid-template-columns:auto 1fr auto; gap:0 10px; align-items:center; padding:10px 16px; cursor:pointer; background:var(--surface); border:0; width:100%; text-align:left; font:inherit; color:inherit; transition:background-color 150ms ease-out; }
.row:nth-child(even) { background:var(--band); }
.row:hover { background:var(--ledger-soft); }
.row[aria-current="true"] { background:var(--ledger-soft); }
.row[aria-current="true"] .acct { color:var(--ledger); }
.row:focus-visible { outline-offset:-2px; }
.row .acct { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.row .meta { grid-column:2 / 4; color:var(--ink-muted); font-size:12px; line-height:18px; }
.row .amt { text-align:right; font-weight:500; }
.skel { height:58px; background:var(--surface); border-bottom:1px solid var(--line); position:relative; }
.skel::before, .skel::after { content:""; position:absolute; left:16px; height:10px; border-radius:var(--r-sm); background:var(--band); }
.skel::before { top:16px; width:55%; } .skel::after { top:34px; width:80%; }

.stamp { display:inline-block; font-size:11px; line-height:14px; font-weight:650; letter-spacing:.05em; text-transform:uppercase; padding:2px 6px; border-radius:var(--r-sm); white-space:nowrap; }
.P1 { color:var(--dispute); background:var(--dispute-soft); }
.P2 { color:var(--writeoff); background:var(--writeoff-soft); }
.P3 { color:var(--ink-muted); background:var(--band); }
.concede { color:var(--writeoff); background:var(--writeoff-soft); }
.c-ready { color:var(--recovered); background:var(--recovered-soft); }
.c-blocked { color:var(--dispute); background:var(--dispute-soft); }
.c-requested, .c-accepted, .c-closed { color:var(--ink-muted); background:var(--band); }
.timeline { margin:0; padding:0; list-style:none; max-width:80ch; }
.timeline li { display:grid; grid-template-columns:96px 1fr; gap:12px; padding:8px 0; border-top:1px solid var(--line); }
.timeline li:first-child { border-top:0; }
.timeline .when { color:var(--ink-muted); font-size:12px; }
.timeline .src { color:var(--ink-muted); font-size:12px; }
.ask { margin:8px 0 0; padding-left:18px; color:var(--dispute); }

.detail { overflow:auto; padding:24px 40px 64px; min-width:0; }
.panel { background:var(--surface); border:1px solid var(--line); border-radius:var(--r-lg); padding:24px 28px; }
.empty { color:var(--ink-muted); padding:40px; max-width:52ch; }
.head { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px; }
.head h2 { margin:0; font-size:24px; line-height:30px; font-weight:650; text-wrap:balance; }
.facts { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:14px 24px; margin:18px 0 22px; padding:14px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.facts dt { font-size:12px; color:var(--ink-muted); }
.facts dd { margin:2px 0 0; }
.facts { margin-inline:0; }
h3 { margin:0 0 8px; font-size:14px; line-height:20px; font-weight:650; }
section.part { margin-top:22px; }
.rec { max-width:75ch; font-size:15px; line-height:23px; margin:0; }
.ev { margin:0; padding:0; list-style:none; max-width:75ch; }
.ev li { padding:8px 12px; background:var(--band); border-radius:var(--r-sm); margin-bottom:4px; }
.ev .src { display:block; color:var(--ink-muted); font-size:12px; }
.letter { border:1px solid var(--line); border-radius:var(--r-md); padding:14px 16px; max-width:80ch; }
.letter .top { display:flex; justify-content:space-between; gap:12px; align-items:start; }
.letter .to { color:var(--ink-muted); font-size:12px; overflow-wrap:anywhere; }
.letter .subj { font-weight:600; margin:4px 0 8px; }
.letter pre { margin:0; white-space:pre-wrap; font:inherit; }
.btn { cursor:pointer; padding:4px 10px; font-size:12px; white-space:nowrap; transition:background-color 150ms ease-out; }
.btn:hover { background:var(--ledger-soft); border-color:var(--ledger); }
.btn:active { transform:translateY(1px); }
.btn.done { color:var(--recovered); border-color:var(--recovered); }

@media (max-width:900px) {
  header { grid-template-columns:minmax(0, 1fr); padding-left:16px; padding-right:16px; }
  .detail { padding-left:16px; padding-right:16px; }
  main { grid-template-columns:minmax(0, 1fr); }
  .seg { min-width:0; }
  .list { max-height:50vh; border-right:0; border-bottom:1px solid var(--line); }
}
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style>
</head>
<body>
<header>
  <div>
    <h1>Dispute desk</h1>
    <p class="lede" id="lede">Reading the latest dispute run from the box.</p>
    <div class="status" id="status" role="status" aria-live="polite"></div>
  </div>
  <div class="aging" id="aging" aria-label="Disputes by days past due"></div>
</header>
<main>
  <section class="list" aria-label="Disputes">
    <div class="tools">
      <input id="q" type="search" placeholder="Search account, invoice or owner" aria-label="Search disputes">
      <select id="fp" aria-label="Priority"><option value="">All priorities</option><option>P1</option><option>P2</option><option>P3</option></select>
      <select id="fr" aria-label="Reason"><option value="">All reasons</option></select>
      <select id="ft" aria-label="Owner"><option value="">All owners</option><option value="account_owner">Account owner</option><option value="billing_ops">Billing ops</option><option value="deal_desk">Deal desk</option></select>
      <select id="fc" aria-label="Action"><option value="">Concede or work</option><option value="1">Concede</option><option value="0">Work it</option></select>
    </div>
    <div class="count"><span id="count"></span><button class="linkbtn" id="clear" hidden>Clear filters</button></div>
    <div id="rows" role="listbox" aria-label="Disputes, highest priority first">
      <div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div><div class="skel"></div>
    </div>
  </section>
  <section class="detail" id="detail" aria-live="polite"></section>
</main>
<script>
const $ = s => document.querySelector(s);
const money = n => "$" + Number(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const TEAMS = {account_owner: "Account owner", billing_ops: "Billing ops", deal_desk: "Deal desk"};
const team = k => TEAMS[k] || String(k ?? "unknown");
const day = iso => { const d = new Date(iso + "T12:00:00"); return isNaN(d) ? esc(iso) : d.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"}); };
const AGES = [["a1", "30 to 60 days", 30, 60], ["a2", "61 to 90 days", 61, 90], ["a3", "91 to 180 days", 91, 100000]];
let all = [], current = null, age = "", loaded = false, reasonKey = "", lastRun = "";

async function get(p) {
  const r = await fetch(p);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

function summary(s) {
  if (!s.disputes) {
    $("#lede").innerHTML = "No dispute run yet. Run <span class=\"mono\">python disputes/disputes.py</span> after triage and this desk fills in.";
    return;
  }
  const p1 = (s.by_priority.P1 || {count: 0}).count;
  const respond = all.filter(d => d.priority === "P1").map(d => d.respond_by).sort()[0];
  const n = v => Number(v || 0).toLocaleString();
  $("#lede").innerHTML = `<b>${n(s.disputes)} disputed small invoices, ${money(s.dollars)}.</b>
    ${n(s.no_reason_recorded)} have no reason on file.
    <span class="p1">${n(p1)} are P1</span>${respond ? " and need a reply by " + day(respond) : ""}.
    ${n(s.stale_flags)} look settled: the customer has since promised to pay.
    ${n(s.concede_recommended.count)} are under $25 and cheaper to credit than to argue (${money(s.concede_recommended.dollars)}).
    ${s.blocker_cases ? `The agent is carrying ${n(s.blocker_cases.open)} missing-PO cases: ${n(s.blocker_cases.by_state.ready)} ready to send, ${n(s.blocker_cases.by_state.blocked)} blocked on a bad PO.` : ""}`;
}

function aging() {
  const focusedAge = document.activeElement && document.activeElement.classList.contains("seg") ? document.activeElement.dataset.age : null;
  const total = all.length || 1;
  const segs = AGES.map(([cls, label, lo, hi]) => {
    const rows = all.filter(d => d.days_past_due >= lo && d.days_past_due <= hi);
    return {cls, label, n: rows.length, dollars: rows.reduce((a, d) => a + d.balance, 0)};
  });
  $("#aging").innerHTML = `<div class="cap"><span>By days past due</span><span>click a band to filter</span></div>
    <div class="strip">${segs.map(s => `<button class="seg ${s.cls}" style="flex-grow:${Math.max(s.n / total, 0.12)}" data-age="${s.cls}" aria-pressed="${age === s.cls}"
      title="${esc(s.label)}"><b>${Number(s.n).toLocaleString()}</b><span>${esc(s.label)} &middot; ${money(s.dollars)}</span></button>`).join("")}</div>`;
  if (focusedAge) { const b = document.querySelector(`.seg[data-age="${focusedAge}"]`); if (b) b.focus({preventScroll: true}); }
}

function shown() {
  const q = $("#q").value.trim().toLowerCase();
  const p = $("#fp").value, r = $("#fr").value, t = $("#ft").value, c = $("#fc").value;
  const band = AGES.find(a => a[0] === age);
  return all.filter(d => (!p || d.priority === p) && (!r || d.reason === r) && (!t || d.route_to === t)
    && (!c || String(+d.concede_recommended) === c)
    && (!band || (d.days_past_due >= band[2] && d.days_past_due <= band[3]))
    && (!q || [d.account_name, d.invoice_id, d.account_owner].some(v => String(v || "").toLowerCase().includes(q))));
}

function filtered() {
  return $("#q").value || $("#fp").value || $("#fr").value || $("#ft").value || $("#fc").value || age;
}

function render() {
  const list = shown();
  const focused = document.activeElement && document.activeElement.classList.contains("row") ? document.activeElement.dataset.id : null;
  $("#count").textContent = `${list.length.toLocaleString()} of ${all.length.toLocaleString()}, highest priority first`;
  $("#clear").hidden = !filtered();
  const inList = list.some(d => d.invoice_id === current);
  $("#rows").innerHTML = list.map((d, i) => {
    const sel = d.invoice_id === current || (!inList && i === 0);
    return `<button class="row" role="option" data-id="${esc(d.invoice_id)}" tabindex="${sel ? 0 : -1}" aria-selected="${d.invoice_id === current}"${d.invoice_id === current ? ' aria-current="true"' : ""}>
      <span class="stamp ${esc(d.priority)}">${esc(d.priority)}</span>
      <span class="acct">${esc(d.account_name)}</span><span class="amt mono">${money(d.balance)}</span>
      <span class="meta"><span class="mono">${esc(d.invoice_id)}</span> &middot; ${esc(d.reason_label)} &middot; ${esc(team(d.route_to))} &middot; reply by ${day(d.respond_by)}${d.concede_recommended ? ' &middot; <span class="stamp concede">concede</span>' : ""}${d.case ? ` &middot; <span class="stamp c-${esc(d.case.state)}">${esc(d.case.label)}</span>` : ""}</span>
    </button>`;
  }).join("") || `<p class="empty">No disputes match. Try a shorter search or clear the filters.</p>`;
  if (focused) {
    const again = [...document.querySelectorAll("#rows .row")].find(r => r.dataset.id === focused);
    if (again) again.focus({preventScroll: true});
  }
}

function letter(title, m, key) {
  return `<section class="part"><h3>${esc(title)}</h3><div class="letter">
    <div class="top"><div class="to">To ${esc(m.to)}${m.cc ? ", cc " + esc(m.cc) : ""}</div>
      <button class="btn" data-copy="${key}">Copy</button></div>
    <div class="subj">${esc(m.subject)}</div><pre id="copy-${key}">${esc(m.body)}</pre></div></section>`;
}

function caseView(d) {
  const c = d.case;
  if (!c) return "";
  const steps = (c.history || []).map(h => `<li><span class="when">${esc(h.at)}</span><span>${esc(h.detail)}
    <span class="src">${esc(h.source === "agent" ? "agent" : h.source)}</span></span></li>`).join("");
  const asks = c.state !== "ready" && (c.open_questions || []).length
    ? `<ul class="ask">${c.open_questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul>` : "";
  const pkg = c.package ? letter("Corrected package: reissued invoice email", c.package.customer_email, "pkg")
    + `<section class="part"><h3>Salesforce update</h3><p class="rec">${esc(c.package.salesforce_update.note)}</p></section>` : "";
  return `<section class="part"><h3>Payment blocker <span class="stamp c-${esc(c.state)}">${esc(c.label)}</span></h3>
    <p class="muted" style="margin:0 0 8px">The agent keeps this case open until the PO checks out against the invoice.${c.state === "blocked" ? " Still missing:" : c.state === "ready" ? "" : " Waiting for:"}</p>
    ${asks}<ol class="timeline">${steps}</ol></section>${pkg}`;
}

let packet = null;
async function show(id) {
  current = id; render();
  let d;
  try { d = await get("/api/disputes/" + encodeURIComponent(id)); }
  catch (e) { if (id === current) $("#detail").innerHTML = '<p class="empty">This dispute dropped out of the latest run, so there is nothing to show. Pick another from the list.</p>'; return; }
  if (id !== current) return;
  packet = d;
  const c = d.salesforce_case;
  const owner = d.route_to === "account_owner" && d.account_owner ? `${esc(d.account_owner)}, account owner` : esc(team(d.route_to));
  const ev = d.evidence.length
    ? `<section class="part"><h3>What the notes say</h3><ul class="ev">${d.evidence.map(e => `<li>${esc(e.text)}<span class="src">${esc(e.date)}, ${esc(e.source)}</span></li>`).join("")}</ul></section>`
    : "";
  $("#detail").innerHTML = `<article class="panel">
    <div class="head"><h2>${esc(d.account_name)}</h2><span class="mono muted">${esc(d.invoice_id)}</span>
      <span class="stamp ${esc(d.priority)}">${esc(d.priority)}</span>${d.concede_recommended ? '<span class="stamp concede">concede</span>' : ""}</div>
    <dl class="facts">
      <div><dt>Balance</dt><dd class="mono">${money(d.balance)}</dd></div>
      <div><dt>Past due</dt><dd><span class="mono">${esc(d.days_past_due)}</span> days</dd></div>
      <div><dt>Reply by</dt><dd>${day(d.respond_by)}</dd></div>
      <div><dt>Goes to</dt><dd>${owner}</dd></div>
      <div><dt>Reason</dt><dd>${esc(d.reason_label)}</dd></div>
      <div><dt>Same account</dt><dd>${d.account_other_queued.count ? `<span class="mono">${Number(d.account_other_queued.count)}</span> more queued, <span class="mono">${money(d.account_other_queued.dollars)}</span>` : "nothing else queued"}</dd></div>
    </dl>
    ${caseView(d)}
    ${d.case && d.case.state === "ready" ? "" : `<section class="part"><h3>Recommended</h3><p class="rec">${esc(d.resolution)}</p></section>`}
    ${ev}
    ${letter("Note to " + (d.route_to === "account_owner" ? "the account owner" : team(d.route_to).toLowerCase()), d.owner_note, "note")}
    ${letter("Holding reply to the customer", d.customer_ack, "ack")}
    <section class="part"><h3>Salesforce case ${esc(c.case_id)} <span class="muted" style="font-weight:400">${esc(c.type)}, ${esc(c.priority)} priority</span></h3>
      <div class="letter"><div class="top"><div class="subj mono" style="font-size:13px;margin-top:0">${esc(c.subject)}</div><button class="btn" data-copy="case">Copy</button></div>
      <pre id="copy-case">${esc(c.description)}</pre></div></section>
  </article>`;
}

async function load() {
  let s, list;
  try { [s, list] = await Promise.all([get("/api/summary"), get("/api/disputes")]); }
  catch (e) {
    $("#status").className = "status err";
    $("#status").textContent = "Can't reach the dispute desk server on the box. Check that disputes/board.py is running; this page retries every 30 seconds.";
    if (!loaded) { $("#rows").innerHTML = '<p class="empty">No disputes loaded yet.</p>'; $("#detail").innerHTML = ""; }
    return;
  }
  loaded = true;
  $("#status").className = "status";
  $("#status").textContent = s.as_of ? `Snapshot ${s.as_of}. Worked on the box; nothing on this page is sent anywhere.` : "";
  const order = {P1: 0, P2: 1, P3: 2};
  all = list.sort((a, b) => order[a.priority] - order[b.priority] || b.balance - a.balance);
  summary(s);
  aging();
  const reasons = [...new Map(all.map(d => [d.reason, d.reason_label])).entries()];
  const key = JSON.stringify(reasons);
  if (key !== reasonKey) {
    reasonKey = key;
    const fr = $("#fr"), keep = fr.value;
    fr.innerHTML = '<option value="">All reasons</option>' + reasons.map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
    fr.value = reasons.some(([k]) => k === keep) ? keep : "";
  }
  if (current && !all.some(d => d.invoice_id === current)) current = null;
  const runKey = s.as_of + ":" + all.length + ":" + s.dollars;
  const changed = runKey !== lastRun;
  lastRun = runKey;
  render();
  const wanted = decodeURIComponent(location.hash.slice(1));
  if (!current && wanted && all.some(d => d.invoice_id === wanted)) show(wanted);
  else if (!current && all.length) show(all[0].invoice_id);
  else if (current && changed) show(current);
  if (!all.length) $("#detail").innerHTML = '<p class="empty">No disputes in the latest run. When triage finds disputed small invoices, they land here with a reason, an owner and drafts.</p>';
}

function move(step) {
  const rows = [...document.querySelectorAll("#rows .row")];
  if (!rows.length) return;
  const i = rows.findIndex(r => r.dataset.id === current);
  const next = rows[Math.min(Math.max(i + step, 0), rows.length - 1)];
  show(next.dataset.id).then(() => {
    if (next.dataset.id !== current) return;
    const el = [...document.querySelectorAll("#rows .row")].find(r => r.dataset.id === next.dataset.id);
    if (el) { el.focus({preventScroll: true}); el.scrollIntoView({block: "nearest"}); }
  });
}

document.addEventListener("input", e => { if (e.target.id === "q") render(); });
document.addEventListener("change", e => { if (e.target.tagName === "SELECT") render(); });
document.addEventListener("click", async e => {
  const seg = e.target.closest(".seg");
  if (seg) { age = age === seg.dataset.age ? "" : seg.dataset.age; aging(); render(); return; }
  if (e.target.id === "clear") { $("#q").value = ""; ["#fp", "#fr", "#ft", "#fc"].forEach(s => $(s).value = ""); age = ""; aging(); render(); return; }
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    const text = $("#copy-" + copy.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; copy.classList.add("done"); }
    catch (err) { const r = document.createRange(); r.selectNodeContents($("#copy-" + copy.dataset.copy)); getSelection().removeAllRanges(); getSelection().addRange(r); copy.textContent = "Selected, press Ctrl+C"; }
    clearTimeout(copy._reset);
    copy._reset = setTimeout(() => { copy.textContent = "Copy"; copy.classList.remove("done"); }, 2000);
    return;
  }
  const row = e.target.closest(".row"); if (row) show(row.dataset.id);
});
document.addEventListener("keydown", e => {
  if (!e.target.closest || !e.target.closest("#rows") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); move(1); }
  if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); move(-1); }
});
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
