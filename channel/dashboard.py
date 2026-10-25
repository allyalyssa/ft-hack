"""Approval dashboard: the queue, the drafted package per invoice, approve / edit / reject, and an Ask sidebar.

python channel/dashboard.py [--host 0.0.0.0] [--port 8080] [--queue out/queue.json]

Standard library only, no external assets, so it keeps working with the uplink pulled. The Collex visual system
is documented in DESIGN.md. The Ask sidebar answers from agent/ask.py with the local model.
"""
import argparse
import html
import json
import subprocess
import sys
from contextlib import closing
from datetime import date, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
import ask  # noqa: E402
import store  # noqa: E402
import uploads  # noqa: E402

esc = html.escape
QUEUE_PATH = store.QUEUE
ROW_LIMIT = 300
STATES = (("awaiting", "Awaiting approval"), ("undrafted", "Not drafted"), ("approved", "Approved"),
          ("rejected", "Rejected"), ("all", "All"))
BUCKETS = (("all", "All buckets"), ("agent", "Agent"), ("writeoff", "Write off"), ("escalate", "Disputed"))
STATE_LABEL = {"drafted": "Awaiting approval", "edited": "Edited", "approved": "Approved", "rejected": "Rejected"}
BUCKET_LABEL = {"agent": "Agent", "writeoff": "Write off", "escalate": "Disputed"}
CASE_TYPE_LABEL = {"collections_outreach": "Collections outreach", "writeoff": "Write-off"}
REJECT_REASONS = ("Wrong facts", "Wrong tone", "Customer already paid", "Not worth contacting", "Other")
FIELD_FILE = {"email_subject": "customer_email.md", "email_body": "customer_email.md", "slack_message": "customer_slack.md",
              "internal_note": "internal_note.md", "memo_rationale": "writeoff_memo.md",
              "case_summary": "salesforce_case.json", "next_action": "salesforce_case.json"}
ASK_EXAMPLES = ("Has INV-116288 been collected?", "Which customers say they already paid?",
                "How much does Umbra Signal owe?", "What needs my approval?")
_cache = {"mtime": None, "queue": None, "exposure": {}}

CSS = """
:root{color-scheme:light;--paper:#fafafa;--surface:#fff;--band:#fff;--line:#e2e2e5;--ink:#1d1d1f;--ink-muted:#6e6e73;
 --ledger:#0071e3;--ledger-soft:#f0f4f9;--on-ledger:#fff;--recovered:#38684f;--recovered-soft:#edf7f1;
 --writeoff:#775b27;--writeoff-soft:#f8f4ec;--dispute:#9b3d3f;--dispute-soft:#fbf1f0;
 --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 --mono:ui-monospace,"SF Mono",Consolas,monospace;--r-sm:4px;--r-md:8px;--r-lg:14px;--ask-w:400px}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/21px var(--sans);accent-color:var(--ledger);
 caret-color:var(--ledger);scrollbar-color:var(--line) transparent}
::selection{background:var(--ledger-soft);color:var(--ink)}
:focus-visible{outline:2px solid var(--ledger);outline-offset:2px}
a{color:var(--ledger);text-underline-offset:3px}
.mono,.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.muted,.sub{color:var(--ink-muted)}
.sub{font-size:12px;line-height:17px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.top{display:flex;align-items:center;gap:24px;padding:0 32px;height:60px;border-bottom:1px solid var(--line);background:#1d1d1f}
.brand{font-weight:650;color:#fff;text-decoration:none;font-size:15px}
.top nav{display:flex;gap:4px}
.top nav a{color:#d2d2d7;text-decoration:none;padding:6px 10px;border-radius:var(--r-md)}
.top nav a:hover{color:#fff;background:#343437}
.top nav a[aria-current="page"]{color:#fff;font-weight:600;background:#343437}
.top .meta{margin-left:auto;font-size:12px;color:#d2d2d7;display:flex;gap:14px;align-items:center;white-space:nowrap}
.local{display:inline-flex;align-items:center;gap:6px;color:#c2dacb;font-weight:600}
.local svg{width:12px;height:12px}
main{padding:24px 32px 64px;max-width:1480px;margin:0 auto;transition:margin-right 200ms ease-out}
body.ask-open main{margin-right:calc(var(--ask-w) + 16px)}
h1{margin:0;font-size:28px;line-height:34px;font-weight:650}
h2{margin:0 0 8px;font-size:14px;line-height:20px;font-weight:650}
.lede{margin:0 0 20px}
.lede p{margin:6px 0 0;max-width:80ch;color:var(--ink-muted)}
.lede b{color:var(--ink);font-weight:600}
.controls{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;margin-bottom:12px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--r-md);background:var(--surface);overflow:hidden}
.seg a{padding:6px 12px;color:var(--ink-muted);text-decoration:none;border-right:1px solid var(--line);white-space:nowrap}
.seg a:last-child{border-right:0}
.seg a:hover{color:var(--ink);background:var(--band)}
.seg a[aria-current="true"]{background:var(--ledger-soft);color:var(--ledger);font-weight:600}
.seg .n{font-family:var(--mono);font-size:12px;margin-left:6px}
.search{margin-left:auto;display:flex;gap:8px;align-items:center}
input,select,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r-md);padding:6px 9px;min-width:0}
input::placeholder,textarea::placeholder{color:var(--ink-muted)}
input:hover,select:hover,textarea:hover{border-color:var(--ink-muted)}
.search input{width:280px}
.count{font-size:12px;color:var(--ink-muted);margin:0 0 8px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line)}
th{text-align:left;font-size:12px;font-weight:600;color:var(--ink-muted);padding:8px 12px;border-bottom:1px solid var(--line);
 background:var(--surface);position:sticky;top:0;z-index:1}
td{padding:8px 12px;vertical-align:top}
tbody tr:nth-child(even) td{background:var(--band)}
tbody tr[data-href]{cursor:pointer}
tbody tr[data-href]:hover td{background:var(--ledger-soft)}
td.num,th.num{text-align:right;white-space:nowrap}
td.id{white-space:nowrap}
td.id a{font-family:var(--mono);font-size:13px;font-weight:500;text-decoration:none}
td.id a:hover{text-decoration:underline}
td.acct{font-weight:600;min-width:150px}
td.acct .sub{font-weight:400}
td.note{max-width:340px;color:var(--ink)}
td.note .text{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.warn{color:var(--dispute)}
.stamp{display:inline-block;font-size:11px;line-height:14px;font-weight:650;letter-spacing:0;
 padding:2px 6px;border-radius:var(--r-sm);white-space:nowrap;background:var(--band);color:var(--ink-muted)}
.s-awaiting,.s-edited{background:var(--ledger-soft);color:var(--ledger)}
.s-approved{background:var(--recovered-soft);color:var(--recovered)}
.s-writeoff{background:var(--writeoff-soft);color:var(--writeoff)}
.s-escalate{background:var(--dispute-soft);color:var(--dispute)}
.s-blocker{background:transparent;color:var(--ink);border:1px solid var(--line)}
.stamps{display:flex;flex-wrap:wrap;gap:4px}
.btn{font:inherit;font-weight:600;font-size:13px;border-radius:var(--r-md);padding:7px 14px;border:1px solid var(--line);
 background:var(--surface);color:var(--ink);cursor:pointer;display:inline-flex;align-items:center;gap:6px;text-decoration:none;
 transition:background-color 150ms ease-out,border-color 150ms ease-out}
.btn:hover{border-color:var(--ledger);background:var(--ledger-soft)}
.btn:active{transform:translateY(1px)}
.btn:disabled,.btn[aria-disabled="true"]{opacity:.55;cursor:progress;transform:none}
.btn.primary{background:var(--ledger);border-color:var(--ledger);color:var(--on-ledger)}
.btn.primary:hover{filter:brightness(1.08)}
.btn.quiet{border-color:transparent;background:transparent;color:var(--ink-muted)}
.btn.quiet:hover{color:var(--dispute);background:var(--dispute-soft);border-color:transparent}
.linkbtn{border:0;background:none;color:var(--ledger);font:inherit;font-weight:600;cursor:pointer;padding:0;
 text-decoration:underline;text-underline-offset:3px}
.flash{display:flex;gap:12px;align-items:center;background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r-md);padding:10px 14px;margin-bottom:16px}
.flash form{margin:0}
.fresh{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:5;box-shadow:0 6px 20px rgba(23,32,51,.18)}
.empty{color:var(--ink-muted);padding:32px;max-width:60ch;background:var(--surface);border:1px dashed var(--line);border-radius:var(--r-lg)}
.empty p{margin:0 0 12px}
.crumbs{margin-bottom:10px;font-size:13px}
.head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin-bottom:6px}
.head .acct{font-size:16px;font-weight:600}
.head .figure{margin-left:auto;font-family:var(--mono);font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.context{margin:0 0 18px;color:var(--ink-muted);max-width:90ch}
.context b{color:var(--ink);font-weight:600}
.grid{display:grid;grid-template-columns:320px minmax(0,1fr);gap:28px;align-items:start}
.facts dl{display:grid;grid-template-columns:118px 1fr;gap:6px 10px;margin:0 0 20px;font-size:13px}
.facts dt{color:var(--ink-muted)}
.facts dd{margin:0;overflow-wrap:anywhere}
.facts dd.num{text-align:left}
.log{margin:0 0 20px;padding:0;list-style:none}
.log li{padding:8px 10px;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-sm);margin-bottom:4px;font-size:13px}
.log .sub{display:block}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);
 box-shadow:none}
.actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--line);
 position:sticky;top:0;background:var(--surface);border-radius:var(--r-lg) var(--r-lg) 0 0;z-index:2}
.actions label{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--ink-muted)}
.actions input{width:160px}
.actions .state{margin-left:auto;font-size:12px;color:var(--ink-muted)}
details.reject{position:relative}
details.reject summary{list-style:none}
details.reject summary::-webkit-details-marker{display:none}
details.reject .pop{position:absolute;top:calc(100% + 6px);left:0;z-index:3;background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r-md);padding:12px;display:flex;flex-direction:column;gap:10px;width:240px;
 box-shadow:0 6px 20px rgba(23,32,51,.14)}
details.reject .pop label{flex-direction:column;align-items:stretch;gap:4px}
.drafts{display:grid;grid-template-columns:1fr 1fr;gap:0}
.draft{padding:18px 20px;border-bottom:1px solid var(--line);min-width:0}
.draft:nth-child(odd){border-right:1px solid var(--line)}
.draft.wide{grid-column:1/-1;border-right:0}
.draft h2{display:flex;gap:8px;align-items:center}
.to{font-size:12px;color:var(--ink-muted);overflow-wrap:anywhere}
.subj{font-weight:600;margin:4px 0 8px}
.body{white-space:pre-wrap;margin:0;font:inherit;max-width:75ch}
.memo{white-space:pre-wrap;margin:0;font:13px/20px var(--sans)}
.chars{font-size:12px;color:var(--ink-muted);margin-top:6px}
.draft textarea{width:100%;min-height:200px;font:14px/21px var(--sans);resize:vertical}
.case dl{display:grid;grid-template-columns:110px 1fr;gap:6px 10px;margin:0;font-size:13px}
.case dt{color:var(--ink-muted)}
.case dd{margin:0}
.ask{position:fixed;top:0;right:0;bottom:0;width:var(--ask-w);max-width:100vw;background:var(--surface);
 border-left:1px solid var(--line);display:flex;flex-direction:column;z-index:10;box-shadow:-8px 0 24px rgba(23,32,51,.08)}
.ask[hidden]{display:none}
.ask-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px 4px}
.ask-head h2{margin:0;font-size:15px}
.icon{border:0;background:none;color:var(--ink-muted);cursor:pointer;padding:6px;border-radius:var(--r-md);display:inline-flex}
.icon:hover{background:var(--band);color:var(--ink)}
.icon svg{width:16px;height:16px}
.ask-sub{margin:0;padding:0 18px 12px;font-size:12px;color:var(--ink-muted);border-bottom:1px solid var(--line)}
.ask-log{flex:1;overflow:auto;padding:14px 18px;display:flex;flex-direction:column;gap:14px}
.ask-q{align-self:flex-end;background:var(--ledger-soft);color:var(--ink);padding:8px 12px;border-radius:var(--r-lg);max-width:85%}
.ask-a p{margin:0 0 8px}
.ask-a table{font-size:12px;border-radius:var(--r-sm)}
.ask-a td,.ask-a th{padding:5px 8px}
.ask-a th{position:static}
.ask-a .sub{margin-top:4px}
.ask-a.err p{color:var(--dispute)}
.skel{height:12px;border-radius:var(--r-sm);background:var(--band);margin:6px 0;width:80%}
.skel+.skel{width:55%}
.ask-try{display:flex;flex-wrap:wrap;gap:6px;padding:0 18px 10px}
.ask-try button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--surface);color:var(--ink);
 border-radius:999px;padding:4px 10px;cursor:pointer}
.ask-try button:hover{border-color:var(--ledger);color:var(--ledger)}
.ask-form{display:flex;gap:8px;padding:12px 18px 16px;border-top:1px solid var(--line)}
.ask-form textarea{flex:1;resize:none;height:42px}
.upload{padding:20px;max-width:760px}
.drop{display:block;border:1.5px dashed var(--line);border-radius:var(--r-md);padding:28px;text-align:center;cursor:pointer;
 background:var(--paper);transition:border-color 150ms ease-out,background-color 150ms ease-out}
.drop:hover,.drop.over{border-color:var(--ledger);background:var(--ledger-soft)}
.drop .sub{display:block;margin-top:6px}
.picked{list-style:none;margin:12px 0;padding:0;font-size:13px}
.picked li{padding:3px 0}
.upload-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.upload-row label{font-size:13px;color:var(--ink-muted)}
.report{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,1fr);gap:28px;align-items:start}
.funnel{list-style:none;margin:0;padding:0}
.funnel li{display:grid;grid-template-columns:72px minmax(0,1fr) 38%;gap:12px;align-items:center;padding:7px 0;
 border-bottom:1px solid var(--line)}
.funnel .n{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-muted)}
.funnel .start .n,.funnel .end .n{color:var(--ink);font-weight:600}
.funnel .end{font-weight:600}
.funnel .bar{height:10px;background:var(--band);border-radius:var(--r-sm);overflow:hidden}
.funnel .bar i{display:block;height:100%;background:var(--line)}
.funnel .start .bar i{background:var(--ink-muted)}
.funnel .end .bar i{background:var(--ledger)}
.split{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:14px 0 0;font-size:13px}
.report-facts{padding:18px 20px}
.report-facts p{margin:0 0 10px;max-width:60ch}
.report-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.report-actions form{margin:0}
@media (max-width:1100px){.report{grid-template-columns:minmax(0,1fr)}}
@media (max-width:1100px){.grid{grid-template-columns:minmax(0,1fr)}.drafts{grid-template-columns:minmax(0,1fr)}
 .draft:nth-child(odd){border-right:0}body.ask-open main{margin-right:auto}td.note{max-width:220px}}
@media (max-width:720px){.top{padding:0 16px;gap:12px}.top .meta span:not(.local){display:none}main{padding:16px}
 .search{margin-left:0;width:100%}.search input{width:100%}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

ICON_X = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true">'
          '<path d="M4 4l8 8M12 4l-8 8"/></svg>')
ICON_LOCAL = ('<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="4" fill="currentColor"/></svg>')

SCRIPT = """
const $ = s => document.querySelector(s);
const escHtml = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
document.querySelectorAll("tr[data-href]").forEach(r => r.addEventListener("click", e => {
  if (!e.target.closest("a,button,input,select,textarea")) location = r.dataset.href;
}));
document.querySelectorAll("form").forEach(f => f.addEventListener("submit", e => {
  const b = e.submitter;
  if (b && b.dataset.busy) { setTimeout(() => { b.disabled = true; b.textContent = b.dataset.busy; }, 0); }
}));
const version = document.body.dataset.version;
if (version) setInterval(async () => {
  try {
    const v = await (await fetch("/api/version", {cache: "no-store"})).text();
    if (v !== version && !$("#fresh")) {
      const b = document.createElement("button");
      b.id = "fresh"; b.className = "btn primary fresh"; b.textContent = "New activity. Refresh";
      b.onclick = () => location.reload(); document.body.append(b);
    }
  } catch (e) {}
}, 15000);

const panel = $("#ask"), toggle = $("#ask-toggle"), logEl = $("#ask-log");
let log = JSON.parse(sessionStorage.getItem("ask-log") || "[]");
function setOpen(open) {
  panel.hidden = !open; document.body.classList.toggle("ask-open", open);
  toggle.setAttribute("aria-expanded", open); sessionStorage.setItem("ask-open", open ? "1" : "");
  if (open) $("#ask-q").focus();
}
function cell(v) {
  const s = escHtml(v);
  return /^INV-\\d+$/.test(String(v)) ? `<a href="/invoice/${s}">${s}</a>` : s;
}
function answerHtml(r) {
  let h = `<p>${escHtml(r.answer)}</p>`;
  if (r.rows && r.rows.length) {
    h += `<table><thead><tr>${r.columns.map(c => `<th>${escHtml(c)}</th>`).join("")}</tr></thead><tbody>` +
         r.rows.map(row => `<tr>${row.map(v => `<td>${cell(v)}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
  }
  if (r.stem) h += `<div class="sub">${escHtml(r.stem)} &middot; ${r.seconds}s</div>`;
  return h;
}
function render() {
  logEl.innerHTML = log.map(e => `<div class="ask-q">${escHtml(e.q)}</div>` +
    (e.r ? `<div class="ask-a${e.r.error ? " err" : ""}">${answerHtml(e.r)}</div>`
         : `<div class="ask-a" aria-busy="true"><div class="skel"></div><div class="skel"></div><div class="sub">Looking it up on this machine</div></div>`)).join("");
  $("#ask-try").hidden = log.length > 0;
  logEl.scrollTop = logEl.scrollHeight;
}
async function send(q) {
  q = q.trim(); if (!q) return;
  const entry = {q}; log.push(entry); render();
  const button = $("#ask-send"); button.disabled = true;
  try {
    const res = await fetch("/api/ask", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question: q})});
    entry.r = res.ok ? await res.json() : {error: true, answer: "The dashboard could not answer that. Try again in a moment."};
  } catch (e) {
    entry.r = {error: true, answer: "No answer from the dashboard. Is it still running?"};
  }
  button.disabled = false;
  log = log.slice(-30); sessionStorage.setItem("ask-log", JSON.stringify(log)); render();
}
toggle.onclick = () => setOpen(panel.hidden);
$("#ask-close").onclick = () => setOpen(false);
document.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) setOpen(false); });
$("#ask-form").onsubmit = e => { e.preventDefault(); const q = $("#ask-q").value; $("#ask-q").value = ""; send(q); };
$("#ask-q").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); } });
document.querySelectorAll("#ask-try button").forEach(b => b.onclick = () => send(b.textContent));
document.querySelectorAll("[data-ask]").forEach(b => b.onclick = () => { setOpen(true); send(b.dataset.ask); });
render();
if (sessionStorage.getItem("ask-open")) setOpen(true);
"""


def queue():
    try:
        mtime = QUEUE_PATH.stat().st_mtime
    except FileNotFoundError:
        return None
    if _cache["mtime"] != mtime:
        try:
            q = store.load_queue(QUEUE_PATH)
            _cache.update(queue=q, mtime=mtime, exposure=store.account_exposure(q["items"]))
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


def state_key(status):
    if not status:
        return "undrafted"
    return {"drafted": "awaiting", "edited": "awaiting"}.get(status["state"], status["state"])


def version():
    statuses = store.all_statuses()
    latest = max((s["at"] for s in statuses.values()), default="")
    return f"{_cache['mtime'] or (QUEUE_PATH.stat().st_mtime if QUEUE_PATH.exists() else 0)}:{len(statuses)}:{latest}"


def plural(n, word, plural_word=None):
    return f"{n:,} {word if n == 1 else (plural_word or word + 's')}"


def short_date(value):
    d = date.fromisoformat(value[:10]) if isinstance(value, str) else value
    return f"{d:%b} {d.day}, {d.year}"


def stamp_time(value):
    t = datetime.fromisoformat(value)
    return f"{t:%b} {t.day}, {t:%H:%M}"


def stamp(label, cls=""):
    return f'<span class="stamp {cls}">{esc(label)}</span>'


def state_stamp(item, status):
    if status:
        return stamp(STATE_LABEL[status["state"]], f"s-{status['state']}")
    if item["bucket"] == "escalate":
        return stamp("With a person", "s-escalate")
    return stamp("Not drafted")


def exposure_text(item):
    count, dollars = _cache["exposure"].get(item["account_id"], (1, item["balance"]))
    if count <= 1:
        return ""
    return f"+{count - 1} more queued, {store.money(dollars - item['balance'])}"


def page(title, body, active="queue", host="localhost", live=True, script=""):
    summary = store.read_json(store.SUMMARY, {})
    last = (summary.get("last_run") or {}).get("started_at")
    model = (summary.get("last_run") or {}).get("model") or store.model_config()[1]
    snapshot = (queue() or {}).get("as_of")
    nav = "".join(f'<a href="{href}"{" aria-current=page" if key == active else ""}>{label}</a>'
                  for key, href, label in (("queue", "/", "Queue"), ("upload", "/upload", "Upload"),
                                           ("crm", "/crm", "Salesforce cases"),
                                           ("disputes", f"http://{esc(host)}:8090/", "Dispute desk")))
    meta = []
    if snapshot:
        meta.append(f"<span>Snapshot {short_date(snapshot)}</span>")
    meta.append(f"<span>{esc(model)}</span>")
    if last:
        meta.append(f"<span>Last run {stamp_time(last)}</span>")
    tries = "".join(f"<button type=button>{esc(q)}</button>" for q in ASK_EXAMPLES)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head>
<body data-version="{esc(version()) if live else ''}">
<header class="top"><a class="brand" href="/">Collex</a><nav aria-label="Main">{nav}</nav>
<div class="meta">{''.join(meta)}<span class="local">{ICON_LOCAL}Running on this machine</span></div>
<button class="btn" id="ask-toggle" aria-expanded="false" aria-controls="ask">Ask</button></header>
<main>{body}</main>
<aside class="ask" id="ask" hidden aria-label="Ask about invoices">
<div class="ask-head"><h2>Ask about invoices</h2><button class="icon" id="ask-close" aria-label="Close">{ICON_X}</button></div>
<p class="ask-sub">Answered by {esc(model)} on this machine from the invoice records. Nothing leaves the box.</p>
<div class="ask-log" id="ask-log" aria-live="polite"></div>
<div class="ask-try" id="ask-try">{tries}</div>
<form class="ask-form" id="ask-form"><label for="ask-q" class="sr">Question</label>
<textarea id="ask-q" placeholder="Ask about an invoice, account or owner"></textarea>
<button class="btn primary" id="ask-send">Ask</button></form>
</aside>
<script>{SCRIPT}{script}</script>
</body></html>"""


def lede(q, all_rows):
    totals = (q or {}).get("totals", {})
    by_bucket = totals.get("by_bucket", {})
    done = store.tally()
    awaiting = sum(1 for _, s in all_rows if state_key(s) == "awaiting")
    minutes = (q or {}).get("policy", {}).get("minutes_per_touch", 6)
    limit = (q or {}).get("policy", {}).get("write_off_max_amount", 5)
    count = lambda b: by_bucket.get(b, {}).get("count", 0)
    return (f'<section class="lede"><h1>Collections queue</h1><p><b>{plural(totals.get("queued", 0), "stale invoice")} worth '
            f'{store.money(totals.get("queued_dollars", 0))}</b>: {count("agent"):,} the agent can work, {count("writeoff"):,} '
            f'under {store.money(limit)} to write off and {count("escalate"):,} disputed. That is about '
            f'{totals.get("queued", 0) * minutes / 60:,.0f} collector hours. <b>{plural(awaiting, "draft")} '
            f'awaiting approval</b>; {done["agent"]["approved"]:,} approved ({store.money(done["agent"]["approved_dollars"])}), '
            f'{done["writeoff"]["approved"]:,} written off ({store.money(done["writeoff"]["approved_dollars"])}).</p></section>')


def index(params, host):
    q = queue()
    all_rows = rows()
    if q is None and not all_rows:
        return page("Collections queue", '<div class="empty"><p>No queue yet.</p><p>Run <span class="mono">python3 '
                    'triage/triage.py</span> and this page fills in.</p></div>', host=host)
    bucket = params.get("bucket", ["all"])[0]
    in_bucket = [(i, s) for i, s in all_rows if bucket == "all" or i["bucket"] == bucket]
    state_counts = {k: sum(1 for _, s in in_bucket if k == "all" or state_key(s) == k) for k, _ in STATES}
    state = params.get("state", ["awaiting" if state_counts["awaiting"] else "all"])[0]
    bucket_counts = {k: sum(1 for i, s in all_rows if (k == "all" or i["bucket"] == k)
                            and (state == "all" or state_key(s) == state)) for k, _ in BUCKETS}
    search = params.get("q", [""])[0].strip()
    selected = [(i, s) for i, s in in_bucket if state == "all" or state_key(s) == state]
    if search:
        needle = search.lower()
        selected = [(i, s) for i, s in selected
                    if needle in f"{i['invoice_id']} {i['account_name']} {i['account_owner']}".lower()]
    if state in ("awaiting", "approved", "rejected"):
        selected.sort(key=lambda r: r[1]["at"], reverse=True)

    def link(key, value):
        p = {"state": state, "bucket": bucket, key: value}
        if search:
            p["q"] = search
        return "/?" + "&".join(f"{k}={quote(v)}" for k, v in p.items())

    states = "".join(f'<a href="{link("state", k)}"{" aria-current=true" if k == state else ""}>{label}'
                     f'<span class="n">{state_counts[k]:,}</span></a>' for k, label in STATES)
    buckets = "".join(f'<a href="{link("bucket", k)}"{" aria-current=true" if k == bucket else ""}>{label}'
                      f'<span class="n">{bucket_counts[k]:,}</span></a>' for k, label in BUCKETS)
    controls = (f'<div class="controls"><nav class="seg" aria-label="Status">{states}</nav>'
                f'<nav class="seg" aria-label="Bucket">{buckets}</nav>'
                f'<form class="search" role="search"><input type="hidden" name="state" value="{esc(state)}">'
                f'<input type="hidden" name="bucket" value="{esc(bucket)}"><label for="q" class="sr">Search</label>'
                f'<input id="q" name="q" type="search" value="{esc(search)}" placeholder="Search invoice, account or owner">'
                f'</form></div>')
    body_rows = "".join(table_row(i, s) for i, s in selected[:ROW_LIMIT])
    shown = (f"Showing {min(len(selected), ROW_LIMIT):,} of {len(selected):,}. Oldest first" if state in ("undrafted", "all")
             else f"Showing {min(len(selected), ROW_LIMIT):,} of {len(selected):,}. Newest first")
    if selected:
        table = ('<table><thead><tr><th>Invoice</th><th>Account</th><th>Owner</th><th class="num">Balance</th>'
                 '<th class="num">Days past due</th><th>Last touch</th><th>Latest note</th><th>Blocker</th><th>Status</th>'
                 f'</tr></thead><tbody>{body_rows}</tbody></table>')
    elif state == "awaiting":
        table = ('<div class="empty"><p>Nothing is waiting for approval.</p><p>The agent drafts 5 invoices every '
                 'cycle, oldest first. Open any invoice under Not drafted to draft it now.</p></div>')
    else:
        table = '<div class="empty"><p>No invoices match. Clear the search or pick another status.</p></div>'
    body = f'{lede(q, all_rows)}{controls}<p class="count">{shown}.</p>{table}'
    return page("Collections queue", body, host=host)


def table_row(i, s):
    url = f"/invoice/{quote(i['invoice_id'])}"
    note = store.latest_note(i)
    key = store.blocker(i)
    if i.get("last_touch_date"):
        touch = (f'{short_date(i["last_touch_date"])}<div class="sub">{esc(i["last_touch_source"].capitalize())}'
                 f'{", sources disagree" if i.get("conflict") else ""}</div>')
    else:
        touch = '<span class="warn">Never</span>'
    note_cell = (f'<div class="text" title="{esc(note[2])}">{esc(note[2])}</div>'
                 f'<div class="sub">{short_date(note[0])}, {esc(note[1].split(",")[0])}</div>') if note else '<span class="sub">None</span>'
    stamps = state_stamp(i, s)
    if i["bucket"] != "agent" and s:
        stamps = stamp(BUCKET_LABEL[i["bucket"]], f"s-{i['bucket']}") + stamps
    elif i["bucket"] == "writeoff":
        stamps = stamp("Write off", "s-writeoff")
    return (f'<tr data-href="{url}"><td class="id"><a href="{url}">{esc(i["invoice_id"])}</a></td>'
            f'<td class="acct">{esc(i["account_name"])}<div class="sub">{esc(exposure_text(i))}</div></td>'
            f'<td>{esc(i["account_owner"])}</td>'
            f'<td class="num">{store.money(i["balance"], i.get("currency", "USD"))}</td><td class="num">{i["days_past_due"]}</td>'
            f'<td>{touch}</td><td class="note">{note_cell}</td>'
            f'<td>{stamp(store.BLOCKER_LABEL[key], "s-blocker") if key else ""}</td>'
            f'<td><div class="stamps">{stamps}</div></td></tr>')


def facts_column(item):
    cur = item.get("currency", "USD")
    touch = (f'{short_date(item["last_touch_date"])}, {esc(item["last_touch_source"].capitalize())} ({item["days_since_touch"]} days ago)'
             if item.get("last_touch_date") else '<span class="warn">Never touched</span>')
    facts = [
        ("Owner", f'{esc(item["account_owner"])}<div class="sub">{esc(item["owner_email"])}</div>'),
        ("Billing contact", esc(item["billing_contact_email"])),
        ("Invoice amount", f'<span class="mono">{store.money(item["amount"], cur)}</span>'),
        ("Paid", f'<span class="mono">{store.money(item["amount_paid"], cur)}</span>'),
        ("Status", esc(item["status"].replace("_", " ").capitalize())),
        ("Issued", short_date(item["issue_date"])),
        ("Due", f'{short_date(item["due_date"])}<div class="sub">{item["days_past_due"]} days past due</div>'),
        ("Last touch", touch),
        ("Why queued", esc(item.get("reason", "").capitalize())),
    ]
    out = '<div class="facts"><h2>Invoice</h2><dl>' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    if item.get("conflict"):
        touches = "; ".join(f"{esc(k)} {short_date(v)}" for k, v in item["conflict"]["touches"].items())
        out += (f'<h2 class="warn">Sources disagree</h2><p class="sub" style="margin:0 0 20px">{touches}. '
                f'{item["conflict"]["gap_days"]} days apart; the latest is used.</p>')
    notes = sorted(item.get("sheet_notes") or [], key=lambda n: n["date"], reverse=True)
    if notes:
        out += '<h2>Collector sheet</h2><ul class="log">' + "".join(
            f'<li>{esc(n["note"] or "")}<span class="sub">{short_date(n["date"])}, {esc(n["collector"] or "")}</span></li>'
            for n in notes) + "</ul>"
    thread = item.get("slack_thread")
    if thread:
        out += f'<h2>Slack {esc(thread["channel"])}</h2><ul class="log">' + "".join(
            f'<li>{esc(m["text"])}<span class="sub">{short_date(m["date"])}, {esc(m["user"])}</span></li>'
            for m in sorted(thread["messages"], key=lambda m: m["ts"], reverse=True)) + "</ul>"
    return out + "</div>"


def draft_sections(item, action, editing):
    files, case = action["files"], action["case"]
    template_files = {FIELD_FILE[f] for f in case.get("template_fields", [])}

    def tag(name):
        return stamp("Template wording") if name in template_files else ""

    def section(name, title, view, wide=False):
        if name not in files:
            return ""
        inner = f'<label class="sr" for="f-{name}">{title}</label><textarea id="f-{name}" name="{name}">{esc(files[name])}</textarea>' \
            if editing else view
        return f'<section class="draft{" wide" if wide else ""}"><h2>{title} {tag(name)}</h2>{inner}</section>'

    out = ""
    if "customer_email.md" in files:
        subject, _, body = files["customer_email.md"].partition("\n\n")
        out += section("customer_email.md", "Customer email",
                       f'<div class="to">To {esc(item["billing_contact_email"])}</div>'
                       f'<div class="subj">{esc(subject.removeprefix("Subject: "))}</div><p class="body">{esc(body.strip())}</p>')
    if "customer_slack.md" in files:
        text = files["customer_slack.md"].strip()
        out += section("customer_slack.md", "Customer Slack",
                       f'<p class="body">{esc(text)}</p><div class="chars">{len(text)} of 600 characters</div>')
    out += section("writeoff_memo.md", "Write-off memo", f'<p class="memo">{esc(files.get("writeoff_memo.md", ""))}</p>', wide=True)
    out += section("internal_note.md", "Note to account owner",
                   f'<div class="to">To {esc(item["account_owner"])}, {esc(item["owner_email"])}</div>'
                   f'<p class="body">{esc(files.get("internal_note.md", "").strip())}</p>')
    with closing(store.crm()) as db:
        row = db.execute("SELECT state, updated_by FROM cases WHERE action_id = ?", (case.get("action_id"),)).fetchone()
    crm_state = f"{STATE_LABEL.get(row['state'], row['state'])}, {row['updated_by']}" if row else "Not in the CRM"
    out += (f'<section class="draft case"><h2>Salesforce case {tag("salesforce_case.json")}</h2><dl>'
            f'<dt>Case</dt><dd class="mono">{esc(case.get("case_number", ""))}</dd>'
            f'<dt>Type</dt><dd>{esc(CASE_TYPE_LABEL.get(case.get("case_type"), ""))}</dd>'
            f'<dt>Status</dt><dd>{esc(crm_state)}</dd>'
            f'<dt>Summary</dt><dd>{esc(case.get("summary", ""))}</dd>'
            f'<dt>Next action</dt><dd>{esc(case.get("next_action", ""))}</dd>'
            f'<dt>Due</dt><dd>{short_date(case["next_action_date"]) if case.get("next_action_date") else ""}</dd>'
            f'<dt>Drafted by</dt><dd>{esc(case.get("model", ""))}, {stamp_time(case["drafted_at"]) if case.get("drafted_at") else ""}</dd>'
            f'</dl></section>')
    return f'<div class="drafts">{out}</div>'


def dispute_panel(invoice_id, item):
    case = store.read_json(store.OUT / "disputes" / f"{invoice_id}.json")
    if case is None:
        return (f'<div class="empty"><p>Disputed, so this one goes to a person. {esc(item["account_owner"])} owns the '
                f'account.</p><p>Run <span class="mono">python3 disputes/disputes.py</span> to work it up.</p></div>')
    evidence = "".join(f'<li>{esc(e["text"])}<span class="sub">{short_date(e["date"])}, {esc(e["source"])}</span></li>'
                       for e in case["evidence"])
    owner, ack = case["owner_note"], case["customer_ack"]
    team = case["route_to"].replace("_", " ").capitalize()
    return (f'<div class="panel"><div class="drafts">'
            f'<section class="draft wide case"><h2>Dispute case {esc(case["case_id"])} {stamp(case["priority"], "s-escalate")}</h2><dl>'
            f'<dt>Reason</dt><dd>{esc(case["reason_label"])}</dd>'
            f'<dt>Respond by</dt><dd>{short_date(case["respond_by"])}</dd>'
            f'<dt>Goes to</dt><dd>{esc(team)}, {esc(case["route_email"] or "")}</dd>'
            f'<dt>Recommended</dt><dd>{esc(case["resolution"])}</dd></dl>'
            + (f'<h2 style="margin-top:14px">Evidence</h2><ul class="log">{evidence}</ul>' if evidence else "") +
            f'</section><section class="draft"><h2>Note to {esc(team.lower())}</h2>'
            f'<div class="subj">{esc(owner["subject"])}</div><p class="body">{esc(owner["body"])}</p></section>'
            f'<section class="draft"><h2>Holding reply to customer</h2>'
            f'<div class="subj">{esc(ack["subject"])}</div><p class="body">{esc(ack["body"])}</p></section></div></div>')


def detail(invoice_id, params, reviewer, host):
    item = find_item(invoice_id)
    if item is None:
        return None
    queue()
    action = store.load_action(invoice_id)
    status = action["status"] if action else None
    editing = bool(action) and params.get("edit") == ["1"]
    url = f"/invoice/{quote(invoice_id)}"
    flash = ""
    if params.get("done") and status:
        flash = (f'<div class="flash" role="status"><span>{esc(STATE_LABEL[status["state"]])} by {esc(status["by"])}'
                 f'{": " + esc(status["reason"]) if status.get("reason") else ""}.</span>'
                 f'<form method="post" action="{url}/undo"><button class="linkbtn">Undo</button></form></div>')
    elif params.get("msg"):
        flash = f'<div class="flash" role="status">{esc(params["msg"][0])}</div>'
    stamps = state_stamp(item, status) if item["bucket"] == "agent" else stamp(BUCKET_LABEL[item["bucket"]], f"s-{item['bucket']}") + (state_stamp(item, status) if status else "")
    exposure = exposure_text(item)
    context = []
    if exposure:
        count, dollars = _cache["exposure"][item["account_id"]]
        context.append(f'{esc(item["account_name"])} has <b>{plural(count - 1, "other queued invoice")}</b> '
                       f'({store.money(dollars - item["balance"])}). <a href="/?state=all&q={quote(item["account_name"])}">See them</a>.')
    key = store.blocker(item)
    if key:
        context.append(f'Blocker from the notes: <b>{esc(store.BLOCKER_LABEL[key])}</b>.')
    head = (f'<div class="crumbs"><a href="/">Queue</a></div><div class="head"><h1>{esc(invoice_id)}</h1>'
            f'<span class="acct">{esc(item["account_name"])}</span><span class="stamps">{stamps}</span>'
            f'<span class="figure">{store.money(item["balance"], item.get("currency", "USD"))}</span></div>'
            f'<p class="context">{" ".join(context)}</p>')
    who = (f'<label for="by">Reviewer</label><input id="by" name="by" value="{esc(reviewer)}" '
           f'autocomplete="name" required>')
    if action is None and item["bucket"] == "escalate":
        right = dispute_panel(invoice_id, item)
    elif action is None:
        model = store.model_config()[1]
        right = (f'<div class="empty"><p>No draft yet. The agent drafts 5 invoices every cycle, oldest first.</p>'
                 f'<form method="post" action="{url}/draft"><button class="btn primary" data-busy="Drafting, about 15 seconds">'
                 f'Draft it now with {esc(model)}</button></form></div>')
    else:
        stamp_line = f'{"Drafted" if status["state"] == "drafted" else STATE_LABEL[status["state"]]} by {esc(status["by"])}, {stamp_time(status["at"])}'
        if editing:
            bar = (f'<div class="actions">{who}<button class="btn primary" formaction="{url}/save" data-busy="Saving">Save edits</button>'
                   f'<a class="btn quiet" href="{url}">Cancel</a><span class="state">{stamp_line}</span></div>')
        else:
            reasons = "".join(f"<option>{r}</option>" for r in REJECT_REASONS)
            bar = (f'<div class="actions">{who}<button class="btn primary" formaction="{url}/approve" data-busy="Approving">Approve</button>'
                   f'<a class="btn" href="{url}?edit=1">Edit</a>'
                   f'<details class="reject"><summary class="btn quiet">Reject</summary><div class="pop">'
                   f'<label for="reason">Reason<select id="reason" name="reason">{reasons}</select></label>'
                   f'<button class="btn" formaction="{url}/reject" data-busy="Rejecting">Reject draft</button></div></details>'
                   f'<span class="state">{stamp_line}</span></div>')
        right = f'<form method="post" class="panel">{bar}{draft_sections(item, action, editing)}</form>'
    body = f'{flash}{head}<div class="grid">{facts_column(item)}<div>{right}</div></div>'
    return page(f"{invoice_id}, Collections", body, host=host)


UPLOAD_SCRIPT = """
const input = $("#files"), drop = $("#drop"), go = $("#go"), picked = $("#picked"), note = $("#upload-status");
const ALLOWED = ["salesforce_invoices.csv", "collector_sheet.csv", "slack_threads.json", "manifest.json"];
let chosen = [];
function show(list) {
  chosen = [...list];
  picked.innerHTML = chosen.map(f => `<li>${escHtml(f.name)} <span class="sub">${Math.max(1, Math.round(f.size / 1024))} KB` +
    `${ALLOWED.includes(f.name) ? "" : ", not part of an export bundle"}</span></li>`).join("");
  go.disabled = !chosen.some(f => f.name === "salesforce_invoices.csv");
  note.textContent = go.disabled && chosen.length ? "salesforce_invoices.csv is required." : "";
}
input.onchange = () => show(input.files);
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); show(e.dataTransfer.files); };
go.onclick = async () => {
  go.disabled = true; go.textContent = "Running the rules"; note.classList.remove("warn");
  const files = {};
  for (const f of chosen) if (ALLOWED.includes(f.name)) files[f.name] = await f.text();
  note.textContent = "Triage, the stale-draft check and disputes are running on this machine. About 10 seconds.";
  try {
    const r = await fetch("/api/upload", {method: "POST", headers: {"Content-Type": "application/json"},
                                          body: JSON.stringify({files, name: $("#batch-name").value})});
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    location = body.url;
  } catch (e) {
    note.textContent = e.message || "The upload failed."; note.classList.add("warn");
    go.disabled = false; go.textContent = "Upload and run the rules";
  }
};
"""


def load_report(upload_id):
    if not store.INVOICE_ID.match(upload_id):
        return None
    return store.read_json(uploads.REPORTS / f"{upload_id}.json")


def draft_candidates(report):
    items = {i["invoice_id"]: i for i in (queue() or {}).get("items", [])}
    return [iid for iid in report["queued_ids"] if iid in items and items[iid]["bucket"] != "escalate"
            and not store.read_status(iid)]


def upload_page(params, host):
    flash = f'<div class="flash" role="status">{esc(params["msg"][0])}</div>' if params.get("msg") else ""
    past = uploads.reports()
    rows_ = "".join(
        f'<tr data-href="/upload/{r["upload_id"]}"><td class="acct"><a href="/upload/{r["upload_id"]}">{esc(r["name"])}</a></td>'
        f'<td>{stamp_time(r["uploaded_at"])}</td><td class="num">{r["funnel"]["received"]:,}</td>'
        f'<td class="num">{len(r["queued_ids"]):,}</td><td class="num">{r["queue_before"]:,} to {r["queue_after"]:,}</td></tr>'
        for r in past)
    history = (f'<h2 style="margin-top:28px">Uploaded so far</h2><table><thead><tr><th>Batch</th><th>Uploaded</th>'
               f'<th class="num">Invoices</th><th class="num">Queued</th><th class="num">Queue size</th></tr></thead>'
               f'<tbody>{rows_}</tbody></table><form method="post" action="/upload/reset" style="margin-top:12px">'
               f'<button class="btn quiet" data-busy="Removing">Remove all uploads</button></form>') if past else ""
    body = f"""{flash}<section class="lede"><h1>Upload an export</h1><p>Add a batch of invoices the way the nightly export
would: the Salesforce invoice file, with the collector sheet and Slack threads if you have them. The same rules the agent
runs every cycle sort it on this machine in about ten seconds, and the next page shows what happened to every invoice.</p>
</section><div class="panel upload"><label class="drop" id="drop" for="files"><b>Choose files</b> or drop them here
<span class="sub">salesforce_invoices.csv (required), collector_sheet.csv, slack_threads.json, manifest.json. Select every
file in one batch folder, for example data/demo/batch-1.</span></label>
<input type="file" id="files" class="sr" multiple accept=".csv,.json"><ul id="picked" class="picked"></ul>
<div class="upload-row"><label for="batch-name">Batch name</label><input id="batch-name" placeholder="batch-1">
<button class="btn primary" id="go" disabled>Upload and run the rules</button></div>
<p class="sub" id="upload-status" role="status"></p></div>{history}"""
    return page("Upload an export", body, active="upload", host=host, script=UPLOAD_SCRIPT)


def report_page(upload_id, params, host):
    report = load_report(upload_id)
    if report is None:
        return None
    f = report["funnel"]
    total = max(f["received"], 1)

    def width(n):
        return f"{100 * n / total:.1f}%"

    steps = "".join(f'<li><span class="n">&minus;{s["removed"]:,}</span><span>{esc(s["label"])}</span>'
                    f'<span class="bar"><i style="width:{width(s["removed"])}"></i></span></li>' for s in f["steps"])
    b = report["buckets"]
    queued_dollars = sum(v["dollars"] for v in b.values())
    funnel_html = (f'<ol class="funnel"><li class="start"><span class="n">{f["received"]:,}</span><span>invoices received</span>'
                   f'<span class="bar"><i style="width:100%"></i></span></li>{steps}'
                   f'<li class="end"><span class="n">{f["passed"]:,}</span><span>queued for collections, '
                   f'{store.money(queued_dollars)}</span><span class="bar"><i style="width:{width(f["passed"])}"></i></span></li></ol>')
    split = (f'<p class="split">{stamp("Agent", "s-awaiting")} {b["agent"]["count"]:,} for the agent ({store.money(b["agent"]["dollars"])}) '
             f'{stamp("Write off", "s-writeoff")} {b["writeoff"]["count"]:,} to write off ({store.money(b["writeoff"]["dollars"])}) '
             f'{stamp("Disputed", "s-escalate")} {b["escalate"]["count"]:,} for a person ({store.money(b["escalate"]["dollars"])})</p>')
    grown = report["queue_after"] - report["queue_before"]
    hours = grown * report["minutes_per_touch"] / 60
    facts = [f'The queue went from {report["queue_before"]:,} to {report["queue_after"]:,} invoices '
             f'({"+" if grown >= 0 else ""}{grown:,}), about {hours:,.1f} more collector hours.']
    if report["accounts"]:
        facts.append(f'{report["accounts_with_more"]:,} of the {plural(report["accounts"], "account")} in this batch already '
                     f'had other stale invoices, so one message can cover several.')
    if report["blockers"]:
        facts.append("Blockers in their notes: " + ", ".join(f"{label} {n}" for label, n in
                                                           sorted(report["blockers"].items(), key=lambda x: -x[1])) + ".")
    items = {i["invoice_id"]: i for i in (queue() or {}).get("items", [])}
    queued = [items[i] for i in report["queued_ids"] if i in items]
    statuses = store.all_statuses()
    table = "".join(table_row(i, statuses.get(i["invoice_id"])) for i in queued[:100])
    model = store.model_config()[1]
    actions_html = ""
    if draft_candidates(report):
        actions_html += (f'<form method="post" action="/upload/{upload_id}/draft"><button class="btn primary" '
                         f'data-busy="Drafting with {esc(model)}, about a minute">Draft 3 of them now with {esc(model)}</button></form>')
    if queued:
        example, account = queued[0]["invoice_id"], esc(queued[0]["account_name"])
        actions_html += (f'<button class="btn" type="button" data-ask="Has {example} been collected?">Ask: has {example} '
                         f'been collected?</button><button class="btn" type="button" data-ask="How much does {account} owe?">'
                         f'Ask: how much does {account} owe?</button>')
    flash = f'<div class="flash" role="status">{esc(params["msg"][0])}</div>' if params.get("msg") else ""
    head = (f'{flash}<div class="crumbs"><a href="/upload">Upload</a></div><section class="lede"><h1>{esc(report["name"])}</h1>'
            f'<p>Uploaded {stamp_time(report["uploaded_at"])}. {f["received"]:,} invoices went through the rules the agent '
            f'runs every cycle, as of {short_date(f["as_of"])}. No model was used to sort them.</p></section>')
    summary = (f'<div class="report"><div>{funnel_html}{split}</div><div class="panel report-facts"><p>'
               + "</p><p>".join(facts) + f'</p><div class="report-actions">{actions_html}</div></div></div>')
    if queued:
        listing = ('<h2 style="margin-top:28px">Queued from this upload</h2><table><thead><tr><th>Invoice</th><th>Account</th>'
                   '<th>Owner</th><th class="num">Balance</th><th class="num">Days past due</th><th>Last touch</th>'
                   f'<th>Latest note</th><th>Blocker</th><th>Status</th></tr></thead><tbody>{table}</tbody></table>')
    else:
        listing = '<div class="empty" style="margin-top:28px"><p>Nothing from this upload needed collections work.</p></div>'
    return page(report["name"], head + summary + listing, active="upload", host=host)


def crm_page(host):
    with closing(store.crm()) as db:
        cases = db.execute(
            "SELECT c.*, i.account_name, i.balance, i.currency, i.status AS invoice_status FROM cases c "
            "LEFT JOIN invoices i USING (invoice_id) ORDER BY c.updated_at DESC, c.case_id DESC LIMIT 500").fetchall()
        total = db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    body_rows = "".join(
        f'<tr data-href="/invoice/{quote(c["invoice_id"])}"><td class="mono">CASE-{c["case_id"]:06d}</td>'
        f'<td class="id"><a href="/invoice/{quote(c["invoice_id"])}">{esc(c["invoice_id"])}</a></td>'
        f'<td class="acct">{esc(c["account_name"] or "")}</td><td>{esc(CASE_TYPE_LABEL.get(c["case_type"], ""))}</td>'
        f'<td class="num">{store.money(c["balance"] or 0, c["currency"] or "USD")}</td>'
        f'<td>{stamp(STATE_LABEL.get(c["state"], c["state"]), "s-" + c["state"])}</td>'
        f'<td>{esc((c["invoice_status"] or "").replace("_", " ").capitalize())}</td>'
        f'<td style="max-width:420px">{esc(c["summary"] or "")}</td>'
        f'<td>{short_date(c["next_action_date"]) if c["next_action_date"] else ""}</td>'
        f'<td>{esc(c["updated_by"] or "")}<div class="sub">{stamp_time(c["updated_at"]) if c["updated_at"] else ""}</div></td></tr>'
        for c in cases)
    table = (f'<table><thead><tr><th>Case</th><th>Invoice</th><th>Account</th><th>Type</th><th class="num">Balance</th>'
             f'<th>Case status</th><th>Invoice status</th><th>Summary</th><th>Next action</th><th>Last change</th></tr></thead>'
             f'<tbody>{body_rows}</tbody></table>') if cases else \
        '<div class="empty"><p>No cases yet. The agent opens one for every invoice it drafts.</p></div>'
    body = (f'<section class="lede"><h1>Salesforce cases</h1><p>{plural(total, "case")} opened by the agent, newest first. '
            f'This is the local stand-in for the Salesforce records the agent would create.</p></section>{table}')
    return page("Salesforce cases", body, active="crm", host=host)


class Handler(BaseHTTPRequestHandler):
    def reviewer(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return unquote(cookie["reviewer"].value) if "reviewer" in cookie else ""

    def host(self):
        return (self.headers.get("Host") or "localhost").rsplit(":", 1)[0]

    def send(self, body, code=200, kind="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                                    "connect-src 'self'; form-action 'self'; img-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", f"reviewer={quote(cookie)}; Path=/; Max-Age=31536000; SameSite=Lax")
        self.end_headers()

    def invoice_route(self, path):
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "invoice" and store.INVOICE_ID.match(parts[1]):
            return parts[1], (parts[2] if len(parts) > 2 else None)
        return None, None

    def not_found(self):
        self.send(page("Not found", '<div class="empty"><p>Nothing here.</p><p><a href="/">Back to the queue</a></p></div>',
                       host=self.host(), live=False), 404)

    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if url.path == "/":
            return self.send(index(params, self.host()))
        if url.path == "/crm":
            return self.send(crm_page(self.host()))
        if url.path == "/api/version":
            return self.send(version(), kind="text/plain; charset=utf-8")
        if url.path == "/upload":
            return self.send(upload_page(params, self.host()))
        if url.path.startswith("/upload/"):
            text = report_page(url.path.split("/")[2], params, self.host())
            return self.send(text) if text else self.not_found()
        invoice_id, action = self.invoice_route(url.path)
        text = detail(invoice_id, params, self.reviewer(), self.host()) if invoice_id and not action else None
        if text is None:
            return self.not_found()
        self.send(text)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 50_000_000)).decode("utf-8", "replace")
        if path == "/api/ask":
            try:
                question = str(json.loads(raw).get("question", ""))
                result = ask.answer(question) if question.strip() else {"answer": ask.help_text(), "rows": [], "columns": []}
            except (ValueError, AttributeError):
                return self.send(json.dumps({"error": True, "answer": "That request was not readable."}), 400, "application/json")
            except OSError:
                result = {"error": True, "answer": "The local model did not answer. Check that Ollama is running."}
            return self.send(json.dumps(result), kind="application/json")
        if path == "/api/upload":
            try:
                body = json.loads(raw)
                files = {str(k): str(v) for k, v in body.get("files", {}).items()}
                report = uploads.save(files, str(body.get("name", "")).strip()[:60])
            except (ValueError, AttributeError) as e:
                return self.send(json.dumps({"error": str(e) or "That upload was not readable."}), 400, "application/json")
            return self.send(json.dumps({"url": f"/upload/{report['upload_id']}"}), kind="application/json")
        if path == "/upload/reset":
            uploads.reset()
            store.build_summary(queue())
            return self.redirect("/upload?msg=" + quote("All uploads removed. The queue is back to the base export."))
        if path.startswith("/upload/") and path.endswith("/draft"):
            report = load_report(path.split("/")[2])
            if report is None:
                return self.not_found()
            todo = draft_candidates(report)[:3]
            if todo:
                cmd = [sys.executable, str(ROOT / "agent" / "actions.py"), "--queue", str(QUEUE_PATH)]
                for iid in todo:
                    cmd += ["--invoice", iid]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                lines = (proc.stdout + proc.stderr).strip().splitlines()
                msg = (f"Drafted {', '.join(todo)} with the local model." if proc.returncode == 0
                       else f"Drafting failed: {lines[-1] if lines else proc.returncode}")
            else:
                msg = "Every invoice the agent can work from this upload already has a draft."
            return self.redirect(f"/upload/{report['upload_id']}?msg={quote(msg)}")
        invoice_id, action = self.invoice_route(path)
        if not invoice_id or action not in ("approve", "reject", "save", "draft", "undo"):
            return self.not_found()
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        by = form.get("by", "").strip() or self.reviewer() or "collector"
        url = f"/invoice/{quote(invoice_id)}"
        if action == "draft":
            proc = subprocess.run([sys.executable, str(ROOT / "agent" / "actions.py"), "--invoice", invoice_id, "--queue", str(QUEUE_PATH)],
                                  capture_output=True, text=True, timeout=600)
            lines = (proc.stdout + proc.stderr).strip().splitlines()
            msg = "Drafted. Review it below." if proc.returncode == 0 else f"Drafting failed: {lines[-1] if lines else proc.returncode}"
            return self.redirect(f"{url}?msg={quote(msg)}")
        try:
            if action == "save":
                store.set_state(invoice_id, "edited", by, {k: v for k, v in form.items() if k in store.EDITABLE})
            elif action == "undo":
                store.undo(invoice_id, by)
            else:
                store.set_state(invoice_id, "approved" if action == "approve" else "rejected", by,
                                reason=form.get("reason") if action == "reject" else None)
        except LookupError as e:
            return self.redirect(f"{url}?msg={quote(str(e))}")
        store.build_summary(queue())
        self.redirect(f"{url}?done=1" if action in ("approve", "reject") else url, cookie=by)

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
