#!/usr/bin/env python3
"""Collections approval dashboard. One file, stdlib only, no network needed.

    python3 channel/dashboard.py                         # http://127.0.0.1:8090
    python3 channel/dashboard.py --host 0.0.0.0 --port 8090
    python3 channel/dashboard.py --queue agent/fixtures/queue.sample.json

Routes: /  queue by bucket, with review state
        /invoice/<id>  the package side by side, Approve / Edit / Reject
        /cases  mock Salesforce cases from agent/crm_mock/crm.sqlite
        /summary  runs from out/summary.json
        /agent  ask the OpenClaw agent in the NemoClaw sandbox; POST /api/agent/ask, GET /api/agent/job/<id>
JSON for the OpenClaw skill, which runs in a sandbox that cannot read out/:
        GET  /api/summary
        GET  /api/queue?bucket=agent&state=awaiting&limit=20
        GET  /api/invoice/<id>
        POST /api/invoice/<id>/approve   {"by": "Mohammed"}
        POST /api/invoice/<id>/reject    {"by": "Mohammed", "reason": "..."}
        POST /api/invoice/<id>/edit      {"by": "Mohammed", "file": "customer_slack.md", "content": "..."}
Approve, Reject and Save write out/actions/<id>/status.json and the latest case row in the CRM.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import re
import shutil
import socketserver
import sqlite3
import sys
import subprocess
import threading
import time
import traceback
import uuid
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
ACTIONS_DIR = OUT_DIR / "actions"
SUMMARY_PATH = OUT_DIR / "summary.json"
CRM_PATH = ROOT / "agent" / "crm_mock" / "crm.sqlite"
POST_PY = Path(__file__).resolve().with_name("post.py")
QUEUE_PATH = OUT_DIR / "queue.json"  # --queue overrides

BUCKETS = (("agent", "Agent works it"), ("writeoff", "Write off"), ("escalate", "Escalate to a human"))
BUCKET_LABEL = dict(BUCKETS)
BUCKET_ACTION = {"agent": "outreach", "writeoff": "writeoff", "escalate": "escalate"}
ARTIFACTS = (
    ("customer_email.md", "Customer email"),
    ("customer_slack.md", "Customer Slack message"),
    ("internal_note.md", "Internal note to the account owner"),
    ("salesforce_case.json", "Salesforce case"),
    ("writeoff_memo.md", "Write-off memo"),
)
EDITABLE = {name for name, _ in ARTIFACTS}
STATE_LABEL = {"drafted": "Drafted", "edited": "Edited", "approved": "Approved", "rejected": "Rejected",
               "superseded": "Superseded", None: "Not drafted"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ROWS_PER_BUCKET = 50
MAX_BODY = 1_000_000

CSS = """
:root{--bg:#f4f5f7;--panel:#fff;--line:#e3e6ea;--line-strong:#cdd2d9;--text:#1c2128;--muted:#5b6470;--faint:#8b939d;
--accent:#1f5fbf;--accent-hover:#1a52a6;--accent-soft:#eaf1fb;--accent-line:#c4d6f3;--accent-ink:#174a96;--chip:#eff1f4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1360px;margin:0 auto;padding:0 24px}
.top{background:var(--panel);border-bottom:1px solid var(--line)}
.top .wrap{display:flex;align-items:center;gap:28px;min-height:52px;flex-wrap:wrap}
.brand{font-weight:650;color:var(--text);letter-spacing:-.01em}.brand:hover{text-decoration:none}
.brand small{font-weight:400;color:var(--muted);margin-left:6px;font-size:13px}
.nav{display:flex;gap:22px;align-self:stretch}
.nav a{display:flex;align-items:center;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px}
.nav a:hover{color:var(--text);text-decoration:none}.nav a.on{color:var(--text);border-bottom-color:var(--accent)}
.who{margin-left:auto;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.who input{width:160px;font:inherit;color:var(--text);padding:5px 9px;border:1px solid var(--line-strong);border-radius:6px;background:var(--panel)}
main{padding:24px 0 64px}
h1{font-size:20px;line-height:1.3;margin:0;font-weight:650;letter-spacing:-.01em}
h2{font-size:15px;margin:0;font-weight:650}
.sub{color:var(--muted);margin:4px 0 20px}
.crumb{font-size:13px;color:var(--muted);margin-bottom:6px}
.group-label{font-size:12px;font-weight:600;color:var(--muted);margin:0 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin:0 0 22px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.tile .k{font-size:12px;color:var(--muted)}
.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile.accent{border-color:var(--accent-line);background:var(--accent-soft)}.tile.accent .v{color:var(--accent-ink)}
.section{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:0 0 20px;overflow:hidden}
.section-head{display:flex;align-items:baseline;gap:12px;padding:13px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.section-head .meta{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.section-head .more{margin-left:auto;font-size:13px}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:12px;font-weight:600;color:var(--muted);padding:8px 16px;border-bottom:1px solid var(--line);background:#fafbfc;white-space:nowrap}
td{padding:9px 16px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}tbody tr:hover td{background:#f8f9fb}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;white-space:nowrap}
.chip{display:inline-block;font-size:12px;color:var(--muted);background:var(--chip);border-radius:4px;padding:0 6px;margin:1px 4px 1px 0;white-space:nowrap}
.badge{display:inline-block;font-size:12px;line-height:18px;padding:0 8px;border-radius:9px;border:1px solid var(--line-strong);color:var(--muted);background:var(--panel);white-space:nowrap}
.badge.drafted,.badge.edited{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}
.badge.approved{color:#fff;background:var(--accent);border-color:var(--accent)}
.badge.rejected{color:#fff;background:#59626d;border-color:#59626d}
.badge.superseded{text-decoration:line-through}
.nowrap{white-space:nowrap}.muted{color:var(--muted)}.small{font-size:12px}.num-text{font-variant-numeric:tabular-nums}
.title-row{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.title-row .amount{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.btn{font:inherit;font-weight:550;padding:7px 18px;border-radius:6px;border:1px solid var(--line-strong);background:var(--panel);color:var(--text);cursor:pointer}
.btn:hover{background:#f1f3f5}.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn.primary:hover{background:var(--accent-hover)}
.btn:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.actionbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:16px 0 18px}
.actionbar form{margin:0}
.notice{border:1px solid var(--accent-line);background:var(--accent-soft);color:var(--accent-ink);border-radius:6px;padding:10px 14px;margin:16px 0 0}
.notice.plain{border-color:var(--line-strong);background:var(--panel);color:var(--text)}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px 24px;padding:16px;margin:0}
.facts dt{font-size:12px;color:var(--muted)}.facts dd{margin:2px 0 0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.reasons{padding:0 16px 14px}
.context{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:4px 24px;padding:14px 16px}
.context h3{font-size:12px;font-weight:600;color:var(--muted);margin:0 0 4px}
.context ul{margin:0 0 10px;padding:0;list-style:none}.context li{margin:0 0 6px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px;align-items:start}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;min-width:0}
.card-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line)}
.card-head h3{font-size:14px;margin:0;font-weight:650}.card-head .file{font-size:12px;color:var(--faint);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.card-body{padding:14px 16px}
.card-body pre{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}
.card-body pre.memo{font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.subject{font-weight:600;margin:0 0 10px;padding-bottom:10px;border-bottom:1px dashed var(--line)}
.count{margin-top:10px}
.kv{display:grid;grid-template-columns:118px 1fr;gap:6px 12px;margin:0}.kv dt{color:var(--muted);font-size:13px}.kv dd{margin:0;overflow-wrap:anywhere}
.crm{margin-top:12px;padding-top:10px;border-top:1px dashed var(--line);font-size:13px;color:var(--muted)}
.card details{border-top:1px solid var(--line)}
.card summary{cursor:pointer;padding:9px 16px;color:var(--accent);font-weight:550;list-style:none}
.card summary::-webkit-details-marker{display:none}.card details[open] summary{color:var(--muted)}
.card form{padding:0 16px 16px;margin:0}
textarea{display:block;width:100%;min-height:200px;margin:0 0 10px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--text);border:1px solid var(--line-strong);border-radius:6px;padding:10px;resize:vertical}
.bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.empty{padding:28px 16px;text-align:center;color:var(--muted)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;background:var(--chip);padding:1px 5px;border-radius:4px}
@media (max-width:640px){.wrap{padding:0 16px}.cards{grid-template-columns:1fr}.who{margin-left:0}.kv{grid-template-columns:1fr}}.ask{padding:16px}.ask textarea{min-height:76px;font:14px/1.5 inherit;font-family:inherit}.ask-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.suggest{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}.suggest button{font:inherit;font-size:13px;padding:4px 10px;border-radius:999px;border:1px solid var(--line-strong);background:var(--chip);color:var(--text);cursor:pointer}.suggest button:hover{border-color:var(--accent-line);background:var(--accent-soft)}.ask-state{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}.qa{border-top:1px solid var(--line);padding:14px 16px}.qa:first-child{border-top:0}.qa .q{font-weight:600;margin:0 0 6px}.qa .when{color:var(--faint);font-size:12px;font-weight:400;margin-left:8px}.qa .a{color:var(--text)}.qa .a p{margin:0 0 8px}.qa .a ul{margin:0 0 8px;padding-left:20px}.qa .err{color:#a8321f}.sandbox-note{display:inline-flex;gap:6px;align-items:center;font-size:12px;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:999px;padding:2px 10px;margin-bottom:14px}.agent-fab{position:fixed;right:20px;bottom:20px;z-index:50;display:flex;align-items:center;gap:8px;font:inherit;font-weight:600;padding:11px 18px;border-radius:999px;border:0;background:var(--accent);color:#fff;cursor:pointer;box-shadow:0 6px 20px rgba(20,40,80,.25)}.agent-fab:hover{background:var(--accent-hover)}.agent-fab .dot{width:8px;height:8px;border-radius:50%;background:#7ee2a8}.agent-fab.busy .dot{background:#ffd166;animation:pulse 1s infinite}@keyframes pulse{50%{opacity:.35}}.agent-panel{position:fixed;right:20px;bottom:76px;z-index:51;width:400px;max-width:calc(100vw - 32px);height:min(640px,calc(100vh - 110px));display:none;flex-direction:column;background:var(--panel);border:1px solid var(--line-strong);border-radius:12px;box-shadow:0 18px 50px rgba(20,40,80,.28);overflow:hidden}.agent-panel.open{display:flex}.ap-head{padding:12px 14px;border-bottom:1px solid var(--line)}.ap-head .t{font-weight:650;display:flex;align-items:center;gap:8px}.ap-head .x{margin-left:auto;border:0;background:none;font-size:20px;line-height:1;color:var(--muted);cursor:pointer}.ap-head .n{font-size:12px;color:var(--accent-ink);margin-top:3px}.ap-body{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;background:#fafbfc}.ap-msg{max-width:88%;padding:8px 11px;border-radius:10px;font-size:13.5px;line-height:1.45;word-wrap:break-word}.ap-msg.me{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:3px}.ap-msg.bot{align-self:flex-start;background:var(--panel);border:1px solid var(--line);border-bottom-left-radius:3px}.ap-msg.bot p{margin:0 0 6px}.ap-msg.bot p:last-child{margin:0}.ap-msg.bot ul{margin:0 0 6px;padding-left:18px}.ap-msg.err{border-color:#f0c4bb;color:#a8321f}.ap-msg .meta{display:block;font-size:11px;color:var(--faint);margin-top:4px}.ap-msg.work{color:var(--muted);font-variant-numeric:tabular-nums}.ap-suggest{display:flex;gap:6px;flex-wrap:wrap;padding:8px 14px 0;border-top:1px solid var(--line)}.ap-suggest button{font:inherit;font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid var(--line-strong);background:var(--chip);color:var(--text);cursor:pointer}.ap-suggest button:hover{border-color:var(--accent-line);background:var(--accent-soft)}.ap-input{display:flex;gap:8px;padding:10px 14px 12px}.ap-input textarea{min-height:0;height:40px;margin:0;flex:1;font:13.5px/1.4 inherit;font-family:inherit;resize:none;padding:9px 10px}.ap-input .btn{padding:7px 14px}.ap-refresh{align-self:flex-start;font-size:12px}.ap-select{position:absolute;z-index:60;display:none;font:inherit;font-size:12.5px;font-weight:600;padding:5px 11px;border-radius:999px;border:0;background:var(--accent);color:#fff;cursor:pointer;box-shadow:0 4px 14px rgba(20,40,80,.25)}.ap-select:hover{background:var(--accent-hover)}.ap-ctx{display:none;margin:8px 14px 0;padding:7px 30px 7px 10px;position:relative;font-size:12.5px;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:8px;max-height:72px;overflow:hidden}.ap-ctx.on{display:block}.ap-ctx b{display:block;font-size:11px;font-weight:600;margin-bottom:2px}.ap-ctx .rm{position:absolute;right:6px;top:4px;border:0;background:none;color:var(--muted);font-size:16px;cursor:pointer}.ap-msg .quote{display:block;border-left:2px solid rgba(255,255,255,.6);padding-left:7px;margin-bottom:5px;opacity:.9;font-size:12.5px}
:root{--selection-ink:#6d465b;--selection-bg:#f2e7ed;--selection-line:#d4b9c8}
::selection{background:var(--selection-bg);color:var(--selection-ink)}
.ap-select,.ap-select:hover{background:var(--selection-bg);color:var(--selection-ink);border:1px solid var(--selection-line);box-shadow:0 4px 16px rgba(45,30,40,.1)}
.ap-ctx{background:var(--selection-bg);color:var(--selection-ink);border-color:var(--selection-line)}.ap-ctx .rm{color:var(--selection-ink)}
.brand{display:inline-flex;align-items:center;gap:8px}.brand small{margin-left:0}.brand .collex-face{width:48px;height:48px}
.collex-face{width:56px;height:56px;object-fit:contain;flex:none}.agent-fab{gap:10px}.agent-fab .collex-face{width:42px;height:42px}.ap-head .t{align-items:center}.ap-head .collex-face{width:68px;height:68px}
.ap-msg.bot .collex-face{display:block;width:38px;height:38px;margin-bottom:6px}.agent-live{position:fixed;left:20px;bottom:20px;z-index:49;width:310px;background:#14201a;color:#e7efe9;border-radius:12px;padding:11px 14px;box-shadow:0 10px 30px rgba(10,20,15,.3);font-size:12.5px;line-height:1.45;cursor:pointer;user-select:none}.agent-live .al-top{display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px}.agent-live .al-dot{width:9px;height:9px;border-radius:50%;background:#7b8a81;flex:none}.agent-live.busy .al-dot{background:#5fe39a;animation:pulse 1s infinite}.agent-live .al-num{font-size:22px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums;transition:color .4s}.agent-live .al-num.bump{color:#5fe39a}.agent-live .al-row{display:flex;align-items:baseline;gap:8px;margin-top:4px}.agent-live .al-sub{color:#a9bcb0}.agent-live .al-plus{color:#5fe39a;font-weight:600;opacity:0;transition:opacity .3s}.agent-live .al-plus.on{opacity:1}.agent-live .al-list{display:none;margin-top:8px;border-top:1px solid rgba(255,255,255,.12);padding-top:6px}.agent-live.expanded .al-list{display:block}.agent-live .al-list div{display:flex;justify-content:space-between;gap:8px;color:#cfdcd3}.agent-live .al-list span:last-child{color:#8fa597;white-space:nowrap}#asktoggle,aside#ask,#ask-selection{display:none!important}.chat-open .shell{grid-template-columns:minmax(0,1fr)!important}
"""

JS = """
(function () {
  var key = "collections.reviewer", input = document.getElementById("reviewer"), name = "";
  try { name = localStorage.getItem(key) || ""; } catch (e) {}
  if (input) {
    input.value = name;
    input.addEventListener("input", function () { try { localStorage.setItem(key, input.value.trim()); } catch (e) {} });
  }
  document.querySelectorAll("form[data-by]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var field = form.querySelector('input[name="by"]');
      if (field && input && input.value.trim()) { field.value = input.value.trim(); }
    });
  });
})();
"""


# ---------------------------------------------------------------- data helpers

def configure(out_dir=None, crm_path=None, queue_path=None):
    """Point the dashboard at other files (tests use a temp folder)."""
    global OUT_DIR, ACTIONS_DIR, SUMMARY_PATH, CRM_PATH, QUEUE_PATH
    if out_dir is not None:
        OUT_DIR = Path(out_dir)
        ACTIONS_DIR = OUT_DIR / "actions"
        SUMMARY_PATH = OUT_DIR / "summary.json"
    if crm_path is not None:
        CRM_PATH = Path(crm_path)
    if queue_path is not None:
        QUEUE_PATH = Path(queue_path)
    _queue_cache.update(key=None, data=None)


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def q(value):
    return urllib.parse.quote(str(value), safe="")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def atomic_write(path, text):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def to_float(value):
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0


def money(value):
    return f"${to_float(value):,.2f}"


def parse_date(value):
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


def fmt_date(value):
    d = parse_date(value)
    return f"{d:%b} {d.day}, {d.year}" if d else ""


def fmt_ts(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value or "")
    return f"{dt:%b} {dt.day}, {dt:%H:%M}"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def display(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def item_id(item):
    inv = item.get("invoice") if isinstance(item.get("invoice"), dict) else {}
    return str(item.get("invoice_id") or inv.get("invoice_id") or "")


def bucket_of(item):
    return item.get("bucket") if item.get("bucket") in BUCKET_ACTION else "agent"


def invoice_of(item):
    """Nested contract shape ({"invoice": {...}}) or the flat shape the root build writes (fields on the item)."""
    if not item:
        return {}
    return item["invoice"] if isinstance(item.get("invoice"), dict) else item


def amount_of(item):
    inv = invoice_of(item)
    return to_float(inv.get("balance") if inv.get("balance") is not None else inv.get("amount"))


def days_past_due(item, as_of):
    if isinstance(item.get("days_past_due"), (int, float)):
        return int(item["days_past_due"])
    for reason in item.get("reasons") or []:
        m = re.match(r"past_due_(\d+)", str(reason))
        if m:
            return int(m.group(1))
    due, today = parse_date(invoice_of(item).get("due_date")), parse_date(as_of)
    return (today - due).days if due and today else None


_queue_cache = {"key": None, "data": None}
_queue_lock = threading.Lock()


def load_queue():
    try:
        st = QUEUE_PATH.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _queue_lock:
        if _queue_cache["key"] != key:
            data = read_json(QUEUE_PATH)
            if not isinstance(data, dict):
                return None
            data["_index"] = {item_id(i): i for i in data.get("items") or [] if isinstance(i, dict)}
            _queue_cache.update(key=key, data=data)
        return _queue_cache["data"]


def current_action_id(item, queue):
    return f"{item_id(item)}:{BUCKET_ACTION[bucket_of(item)]}:{queue.get('snapshot_id') or 'local'}"


def load_packages():
    packages = {}
    try:
        entries = list(os.scandir(ACTIONS_DIR))
    except OSError:
        return packages
    for entry in entries:
        if entry.is_dir():
            status = read_json(Path(entry.path) / "status.json")
            if isinstance(status, dict):
                packages[entry.name] = status
    return packages


def live_counts():
    """Review state right now: status.json files, plus approved dollars from the CRM."""
    counts = {"packages": 0, "awaiting": 0, "drafted": 0, "edited": 0, "approved": 0, "rejected": 0,
              "approved_dollars": 0.0, "written_off": 0.0}
    for status in load_packages().values():
        counts["packages"] += 1
        state = status.get("state")
        if state in ("drafted", "edited"):
            counts["awaiting"] += 1
        if state in ("drafted", "edited", "approved", "rejected"):
            counts[state] += 1
    if CRM_PATH.exists():
        try:
            with closing(sqlite3.connect(str(CRM_PATH), timeout=10)) as con:
                rows = con.execute(
                    "SELECT c.case_type, i.amount FROM cases c JOIN invoices i ON i.invoice_id = c.invoice_id "
                    "WHERE c.case_id IN (SELECT MAX(case_id) FROM cases GROUP BY invoice_id) AND c.state = 'approved'"
                ).fetchall()
            for case_type, amount in rows:
                counts["approved_dollars"] += amount or 0.0
                if case_type == "writeoff":
                    counts["written_off"] += amount or 0.0
        except sqlite3.Error:
            pass
    return counts


def crm_lookup(invoice_id):
    if not CRM_PATH.exists():
        return None, None
    try:
        with closing(sqlite3.connect(str(CRM_PATH), timeout=10)) as con:
            con.row_factory = sqlite3.Row
            inv = con.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
            case = con.execute("SELECT * FROM cases WHERE invoice_id = ? ORDER BY case_id DESC LIMIT 1",
                               (invoice_id,)).fetchone()
        return (dict(inv) if inv else None), (dict(case) if case else None)
    except sqlite3.Error:
        return None, None


def crm_update(invoice_id, **fields):
    """Update the latest case row for the invoice. Returns its case_id, or None when there is none."""
    if not CRM_PATH.exists():
        return None
    try:
        with closing(sqlite3.connect(str(CRM_PATH), timeout=10)) as con:
            with con:
                row = con.execute("SELECT MAX(case_id) FROM cases WHERE invoice_id = ?", (invoice_id,)).fetchone()
                if not row or row[0] is None:
                    return None
                assignments = ", ".join(f"{column} = ?" for column in fields)
                con.execute(f"UPDATE cases SET {assignments} WHERE case_id = ?", (*fields.values(), row[0]))
                return row[0]
    except sqlite3.Error as exc:
        print(f"dashboard: CRM update failed for {invoice_id}: {exc}", file=sys.stderr)
        return None


def set_state(invoice_id, state, by, reason=None):
    """The one write path for a decision, used by the HTML buttons and the JSON API. Returns (status, crm case id)."""
    path = ACTIONS_DIR / invoice_id / "status.json"
    status = read_json(path)
    status = status if isinstance(status, dict) else {}
    status.update(state=state, by=by, at=now_iso())  # keeps action_id and drafted_at
    if reason:
        status["reason"] = reason
    else:
        status.pop("reason", None)
    atomic_write(path, json.dumps(status, indent=2, ensure_ascii=False) + "\n")
    return status, crm_update(invoice_id, state=state)


class EditError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def save_artifact(invoice_id, name, content, by):
    """Replace one artifact and mark the package edited. Raises EditError on bad input."""
    folder = ACTIONS_DIR / invoice_id
    if name not in EDITABLE or not (folder / name).exists():
        raise EditError(f"{invoice_id} has no {name}", 404)
    content = str(content).replace("\r\n", "\n").strip() + "\n"
    if name == "salesforce_case.json":
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("the case must be a JSON object")
        except ValueError as exc:
            raise EditError(f"salesforce_case.json is not valid JSON ({exc})")
        old = read_json(folder / name) or {}
        data["invoice_id"], data["action_id"] = old.get("invoice_id", invoice_id), old.get("action_id", "")
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        crm_update(invoice_id, summary=str(data.get("summary", "")), next_action=str(data.get("next_action", "")),
                   next_action_date=str(data.get("next_action_date", "")))
    atomic_write(folder / name, content)
    return set_state(invoice_id, "edited", by)


def notify(text):
    """Echo a decision to the channel in the background. The dashboard never waits on the network."""
    def worker():
        try:
            spec = importlib.util.spec_from_file_location("collections_channel_post", POST_PY)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.post_text(text)
        except Exception as exc:
            print(f"dashboard: channel post skipped: {exc}", file=sys.stderr)
    if POST_PY.exists():
        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------- html pieces

def page(title, body, nav="", refresh=0):
    links = (("/", "Queue", "queue"), ("/cases", "Cases", "cases"), ("/summary", "Runs", "summary"))
    nav_html = "".join(f'<a href="{href}"{" class=on" if key == nav else ""}>{label}</a>' for href, label, key in links)
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)} · Collections</title>{meta}<style>{CSS}</style></head><body>"
            f'<header class="top"><div class="wrap"><a class="brand" href="/"><img class="collex-face" src="/assets/collex-companion.png" alt="" width="48" height="48">Collex<small>approval desk</small></a>'
            f'<nav class="nav">{nav_html}</nav>'
            f'<label class="who">Reviewer <input id="reviewer" placeholder="Your name" autocomplete="name"></label>'
            f'</div></header><main class="wrap">{body}</main>{agent_panel()}<script>{JS}</script><script>{AGENT_PANEL_JS}</script></body></html>')


def tile(label, value, accent=False):
    return (f'<div class="tile{" accent" if accent else ""}"><div class="k">{esc(label)}</div>'
            f'<div class="v">{esc(value)}</div></div>')


def badge(state):
    cls = state if state in STATE_LABEL else ""
    return f'<span class="badge {esc(cls)}">{esc(STATE_LABEL.get(state, state))}</span>'


SOURCE_LABEL = {"salesforce": "Salesforce", "collector_sheet": "collector sheet", "sheet": "collector sheet", "slack": "Slack"}


def touch_where(channel, source):
    label = SOURCE_LABEL.get(str(source or "").lower(), str(source or ""))
    channel = str(channel or "")
    if not channel or channel.lower() in (str(source or "").lower(), label.lower()):
        return label
    return f"{channel} ({label})" if label else channel


def soft_breaks(text):
    """Let long emails wrap after @ and dots instead of mid-word."""
    return esc(text).replace("@", "@<wbr>").replace(".", ".<wbr>")


def chips(reasons):
    return "".join(f'<span class="chip">{esc(str(r).replace("_", " "))}</span>' for r in reasons or [])


def not_found(message):
    body = (f'<div class="crumb"><a href="/">Queue</a></div><h1>Not found</h1>'
            f'<p class="sub">{esc(message)}</p>')
    return page("Not found", body)


# ---------------------------------------------------------------- pages

def render_queue(query):
    refresh = 0
    try:
        refresh = max(10, int(query.get("refresh", ["0"])[0])) if query.get("refresh") else 0
    except ValueError:
        refresh = 0
    queue = load_queue()
    if queue is None:
        body = ('<h1>Queue</h1><p class="sub">Nothing to review yet.</p><div class="section"><div class="empty">'
                f"No queue at <code>{esc(display(QUEUE_PATH))}</code>. Run <code>python3 triage/triage.py</code>, "
                "then <code>python3 agent/actions.py --limit 5</code>.</div></div>")
        return page("Queue", body, "queue", refresh)

    packages = load_packages()
    items = [i for i in queue.get("items") or [] if isinstance(i, dict)]
    totals = queue.get("totals") if isinstance(queue.get("totals"), dict) else {}
    as_of = queue.get("as_of")
    groups = {b: [] for b, _ in BUCKETS}
    review = {"awaiting": 0, "approved": 0, "rejected": 0, "none": 0}
    for item in items:
        status = packages.get(item_id(item))
        state = status.get("state") if status else None
        if state in ("drafted", "edited"):
            rank, key = 0, "awaiting"
        elif state in ("approved", "rejected"):
            rank, key = 1, state
        else:
            rank, key = 2, "none"
        review[key] += 1
        groups.setdefault(bucket_of(item), []).append((rank, item, state))

    dollars = totals.get("dollars_queued", sum(to_float(invoice_of(i).get("amount")) for i in items))
    triage_tiles = [
        tile("Invoices", f"{totals['invoices']:,}") if "invoices" in totals else "",
        tile("Open", f"{totals['open']:,}") if "open" in totals else "",
        tile("Under agent max", f"{totals['under_agent_max']:,}") if "under_agent_max" in totals else "",
        tile("Queued", f"{totals.get('queued', len(items)):,}"),
        tile("Dollars queued", money(dollars)),
    ]
    review_tiles = [
        tile("Awaiting review", f"{review['awaiting']:,}", accent=review["awaiting"] > 0),
        tile("Approved", f"{review['approved']:,}"),
        tile("Rejected", f"{review['rejected']:,}"),
        tile("Not drafted", f"{review['none']:,}"),
    ]
    snapshot = queue.get("snapshot_id") or "local"
    parts = [
        "<h1>Queue</h1>",
        f'<p class="sub">As of {esc(fmt_date(as_of) or as_of)} · snapshot <span class="mono">{esc(snapshot)}</span> · '
        f'{esc(display(QUEUE_PATH))}</p>',
        '<div class="group-label">Triage</div><div class="tiles">' + "".join(triage_tiles) + "</div>",
        '<div class="group-label">Review</div><div class="tiles">' + "".join(review_tiles) + "</div>",
    ]
    show_all = query.get("all", [""])[0]
    for bucket, label in BUCKETS:
        rows = sorted(groups.get(bucket, []), key=lambda r: r[0])  # stable: amount order holds inside a group
        bucket_dollars = sum(to_float(invoice_of(r[1]).get("amount")) for r in rows)
        awaiting = sum(1 for r in rows if r[0] == 0)
        shown = rows if show_all == bucket else rows[:ROWS_PER_BUCKET]
        more = (f'<a class="more" href="/?all={bucket}#{bucket}">Show all {len(rows):,}</a>'
                if len(rows) > len(shown) else "")
        head = (f'<div class="section-head" id="{bucket}"><h2>{esc(label)}</h2>'
                f'<span class="meta">{len(rows):,} invoices · {money(bucket_dollars)} · {awaiting:,} awaiting review</span>'
                f"{more}</div>")
        if not rows:
            parts.append(f'<div class="section">{head}<div class="empty">No invoices in this bucket.</div></div>')
            continue
        body_rows = []
        for rank, item, state in shown:
            inv = invoice_of(item)
            invoice = item_id(item)
            dpd = days_past_due(item, as_of)
            body_rows.append(
                f'<tr><td class="mono"><a href="/invoice/{q(invoice)}">{esc(invoice)}</a></td>'
                f'<td>{esc(inv.get("account_name"))}</td><td class="num">{money(inv.get("amount"))}</td>'
                f'<td class="num">{"" if dpd is None else dpd}</td><td>{chips(item.get("reasons"))}</td>'
                f"<td>{badge(state)}</td></tr>")
        parts.append(
            f'<div class="section">{head}<div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Account</th>'
            f'<th class="num">Amount</th><th class="num">Days past due</th><th>Reasons</th><th>State</th></tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table></div></div>')
    return page("Queue", "".join(parts), "queue", refresh)


def card(name, label, folder, invoice_id, drafts, crm_case):
    path = folder / name
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    draft = (drafts or {}).get(name, text)
    if name == "customer_email.md":
        subject, _, body = text.partition("\n")
        view = f'<div class="subject">{esc(subject.strip())}</div><pre>{esc(body.strip())}</pre>'
    elif name == "customer_slack.md":
        n = len(text.strip())
        count = f"{n} of 600 characters" if n < 600 else f"{n} characters, over the 600 limit"
        view = f'<pre>{esc(text.strip())}</pre><div class="count small muted">{count}</div>'
    elif name == "salesforce_case.json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            rows = (("Case type", data.get("case_type")), ("Summary", data.get("summary")),
                    ("Next action", data.get("next_action")), ("Next action date", fmt_date(data.get("next_action_date"))),
                    ("Drafted at", fmt_ts(data.get("drafted_at"))), ("Model", data.get("model")),
                    ("Action ID", data.get("action_id")))
            view = '<dl class="kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rows) + "</dl>"
        else:
            view = f'<pre class="memo">{esc(text.strip())}</pre>'
        if crm_case:
            view += (f'<div class="crm">Mock Salesforce case #{esc(crm_case.get("case_id"))}, state '
                     f'{badge(crm_case.get("state"))}</div>')
    else:
        view = f'<pre{" class=memo" if name == "writeoff_memo.md" else ""}>{esc(text.strip())}</pre>'
    rows = max(8, min(24, draft.count("\n") + 3))
    edit = (f'<details{" open" if name in (drafts or {}) else ""}><summary>Edit</summary>'
            f'<form method="post" action="/invoice/{q(invoice_id)}/save" data-by>'
            f'<input type="hidden" name="file" value="{esc(name)}"><input type="hidden" name="by" value="">'
            f'<textarea name="content" rows="{rows}" spellcheck="true">{esc(draft)}</textarea>'
            f'<div class="bar"><button class="btn primary" type="submit">Save</button>'
            f'<span class="small muted">Saving marks the package edited.</span></div></form></details>')
    return (f'<section class="card"><div class="card-head"><h3>{esc(label)}</h3><span class="file">{esc(name)}</span></div>'
            f'<div class="card-body">{view}</div>{edit}</section>')


def render_invoice(invoice_id, query, error="", drafts=None):
    queue = load_queue() or {}
    item = (queue.get("_index") or {}).get(invoice_id)
    folder = ACTIONS_DIR / invoice_id
    status = read_json(folder / "status.json")
    status = status if isinstance(status, dict) else None
    case = read_json(folder / "salesforce_case.json")
    case = case if isinstance(case, dict) else {}
    crm_inv, crm_case = crm_lookup(invoice_id)
    if item is None and status is None and crm_inv is None:
        return not_found(f"No invoice {invoice_id} in the queue, out/actions or the CRM."), 404

    inv = invoice_of(item)
    crm_inv = crm_inv or {}
    bucket = bucket_of(item) if item else ("writeoff" if case.get("case_type") == "writeoff" else "")
    account = inv.get("account_name") or crm_inv.get("account_name") or ""
    amount = inv.get("amount") if inv else crm_inv.get("amount")
    state = status.get("state") if status else None

    parts = [f'<div class="crumb"><a href="/">Queue</a>'
             + (f' / <a href="/#{bucket}">{esc(BUCKET_LABEL.get(bucket, bucket))}</a>' if bucket else "") + "</div>",
             f'<div class="title-row"><h1 class="mono" style="font-size:20px">{esc(invoice_id)}</h1>'
             f'<h1>{esc(account)}</h1><span class="amount">{money(amount)}</span>{badge(state)}</div>']
    meta = []
    if status and status.get("action_id"):
        meta.append(f'action <span class="mono">{esc(status["action_id"])}</span>')
    if case.get("model"):
        meta.append(f"drafted by {esc(case['model'])}" + (f" at {esc(fmt_ts(case.get('drafted_at')))}" if case.get("drafted_at") else ""))
    if meta:
        parts.append(f'<p class="sub" style="margin-bottom:0">{" · ".join(meta)}</p>')

    done = query.get("done", [""])[0]
    if error:
        parts.append(f'<div class="notice plain"><strong>Not saved.</strong> {esc(error)}</div>')
    elif done in ("approved", "rejected", "edited") and status:
        who = f" by {status.get('by')}" if status.get("by") else ""
        crm_note = f" and mock Salesforce case #{crm_case['case_id']}" if crm_case else ""
        parts.append(f'<div class="notice">{esc(STATE_LABEL[done])}{esc(who)}. Updated status.json{esc(crm_note)}.</div>')
    if item and status and status.get("action_id") and status.get("action_id") != current_action_id(item, queue):
        parts.append(f'<div class="notice plain">Drafted from an earlier snapshot. The current queue would make this '
                     f'<span class="mono">{esc(current_action_id(item, queue))}</span>. The agent drafts it again once '
                     f'the package is 8 days old, when it is rejected, or when the bucket changes.</div>')

    if status:
        who = f" by {status.get('by')}" if status.get("by") else ""
        line = (f"{STATE_LABEL.get(state, state)}{who}, {fmt_ts(status.get('at'))}" if state != "drafted"
                else f"Drafted {fmt_ts(status.get('at'))}, waiting for a reviewer")
        if status.get("reason"):
            line += f". Reason: {status['reason']}"
        parts.append(
            f'<div class="actionbar">'
            f'<form method="post" action="/invoice/{q(invoice_id)}/approve" data-by><input type="hidden" name="by" value="">'
            f'<button class="btn primary" type="submit">Approve</button></form>'
            f'<form method="post" action="/invoice/{q(invoice_id)}/reject" data-by><input type="hidden" name="by" value="">'
            f'<button class="btn" type="submit">Reject</button></form>'
            f'<span class="muted">{esc(line)}</span></div>')
    else:
        parts.append('<div style="height:16px"></div>')

    touch = item.get("last_touch") if item and isinstance(item.get("last_touch"), dict) else {}
    touch_text = (f"{fmt_date(touch.get('date'))}, {touch_where(touch.get('channel'), touch.get('source'))}"
                  if touch.get("date") else ("none on record" if item else ""))
    contact = [x for x in (inv.get("contact_name"), inv.get("contact_email"), inv.get("contact_slack")) if x]
    dpd = days_past_due(item, queue.get("as_of")) if item else None
    facts = (("Bucket", BUCKET_LABEL.get(bucket, "Not in the current queue")),
             ("Days past due", "" if dpd is None else dpd),
             ("Due date", fmt_date(inv.get("due_date") or crm_inv.get("due_date"))),
             ("Last touch", touch_text),
             ("Customer contact", contact),  # list: one line each
             ("Account owner", inv.get("account_owner") or ""),
             ("Salesforce status", inv.get("status") or crm_inv.get("status") or ""),
             ("Invoice date", fmt_date(inv.get("invoice_date"))))
    facts_html = "".join(
        f"<div><dt>{esc(k)}</dt><dd>{'<br>'.join(soft_breaks(x) for x in v) if isinstance(v, list) else esc(v)}"
        f"{'' if v not in ('', None, []) else '<span class=muted>none</span>'}</dd></div>" for k, v in facts)
    reasons = f'<div class="reasons">{chips(item.get("reasons"))}</div>' if item and item.get("reasons") else ""
    parts.append(f'<div class="section"><dl class="facts">{facts_html}</dl>{reasons}</div>')

    if item:
        notes = [n for n in item.get("notes") or [] if isinstance(n, dict) and n.get("note")]
        slack = [m for m in item.get("slack") or [] if isinstance(m, dict) and m.get("text")]
        conflicts = [c for c in item.get("conflicts") or [] if c]
        blocks = []
        if notes:
            blocks.append("<div><h3>Collector notes</h3><ul>" + "".join(
                f'<li><span class="muted">{esc(fmt_date(n.get("note_date")))} · {esc(n.get("collector"))}</span><br>{esc(n.get("note"))}</li>'
                for n in notes) + "</ul></div>")
        if slack:
            blocks.append("<div><h3>Slack</h3><ul>" + "".join(
                f'<li><span class="muted">{esc(fmt_ts(m.get("ts")))} · {esc(m.get("user"))}</span><br>{esc(m.get("text"))}</li>'
                for m in slack) + "</ul></div>")
        if conflicts:
            blocks.append("<div><h3>Conflicts between sources</h3><ul>" + "".join(
                f"<li>{esc(c)}</li>" for c in conflicts) + "</ul></div>")
        if blocks:
            parts.append(f'<div class="section"><div class="context">{"".join(blocks)}</div></div>')

    if status:
        cards = "".join(card(name, label, folder, invoice_id, drafts, crm_case) for name, label in ARTIFACTS)
        parts.append(f'<div class="cards">{cards}</div>')
    else:
        parts.append('<div class="section"><div class="empty">No package drafted yet. '
                     f"Run <code>python3 agent/actions.py --invoice {esc(invoice_id)}</code>.</div></div>")
    return page(invoice_id, "".join(parts), "queue"), (400 if error else 200)


def render_cases():
    rows, error = [], ""
    if CRM_PATH.exists():
        try:
            with closing(sqlite3.connect(str(CRM_PATH), timeout=10)) as con:
                rows = con.execute(
                    "SELECT c.case_id, c.invoice_id, i.account_name, i.amount, c.case_type, c.state, c.next_action, "
                    "c.next_action_date, c.drafted_at, c.model FROM cases c LEFT JOIN invoices i ON i.invoice_id = c.invoice_id "
                    "ORDER BY c.case_id DESC LIMIT 500").fetchall()
        except sqlite3.Error as exc:
            error = str(exc)
    counts = {}
    for row in rows:
        counts[row[5]] = counts.get(row[5], 0) + 1
    parts = ["<h1>Salesforce cases</h1>",
             f'<p class="sub">Mock Salesforce in {esc(display(CRM_PATH))}. One case per drafted package; '
             "the newest case for an invoice carries its review state.</p>",
             '<div class="tiles">' + tile("Cases shown", f"{len(rows):,}") +
             "".join(tile(STATE_LABEL.get(s, s), f"{counts.get(s, 0):,}") for s in ("drafted", "edited", "approved", "rejected")) +
             "</div>"]
    if not rows:
        parts.append(f'<div class="section"><div class="empty">{esc(error) if error else "No cases yet. They appear after the first agent run."}</div></div>')
        return page("Cases", "".join(parts), "cases")
    body = "".join(
        f'<tr><td class="num">{r[0]}</td><td class="mono"><a href="/invoice/{q(r[1])}">{esc(r[1])}</a></td>'
        f'<td>{esc(r[2])}</td><td class="num">{money(r[3])}</td><td>{esc(r[4])}</td><td>{badge(r[5])}</td>'
        f'<td>{esc(r[6])}</td><td class="num">{esc(fmt_date(r[7]))}</td><td class="num">{esc(fmt_ts(r[8]))}</td>'
        f'<td class="nowrap">{esc(r[9])}</td></tr>' for r in rows)
    parts.append('<div class="section"><div class="table-wrap"><table><thead><tr><th class="num">Case</th><th>Invoice</th>'
                 '<th>Account</th><th class="num">Amount</th><th>Type</th><th>State</th><th>Next action</th>'
                 '<th class="num">Next action date</th><th class="num">Drafted</th><th>Model</th></tr></thead>'
                 f"<tbody>{body}</tbody></table></div></div>")
    return page("Cases", "".join(parts), "cases")


def render_summary():
    runs = read_json(SUMMARY_PATH)
    if isinstance(runs, dict):
        runs = runs.get("runs")
    runs = [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
    parts = ["<h1>Runs</h1>", f'<p class="sub">Every agent run appends a record to {esc(display(SUMMARY_PATH))}.</p>']
    if not runs:
        parts.append('<div class="section"><div class="empty">No runs yet. Run <code>python3 agent/actions.py --limit 5</code>.</div></div>')
        return page("Runs", "".join(parts), "summary")
    last, live = runs[-1], live_counts()
    parts.append('<div class="group-label">Last run</div><div class="tiles">' + "".join([
        tile("Run at", fmt_ts(last.get("run_at"))),
        tile("Model", last.get("model", "")),
        tile("Drafted", f"{last.get('drafted', 0):,}"),
        tile("Duration", f"{last.get('duration_s', 0)}s"),
    ]) + '</div><div class="group-label">Review now</div><div class="tiles">' + "".join([
        tile("Packages", f"{live['packages']:,}"),
        tile("Awaiting review", f"{live['awaiting']:,}", accent=live["awaiting"] > 0),
        tile("Approved", f"{live['approved']:,}"),
        tile("Approved dollars", money(live["approved_dollars"])),
        tile("Written off", money(live["written_off"])),
    ]) + "</div>")
    body = []
    for r in reversed(runs[-300:]):
        b = r.get("by_bucket") or {}
        drafted = lambda k: (b.get(k) or {}).get("drafted", 0)  # noqa: E731
        body.append(
            f'<tr><td class="num">{esc(fmt_ts(r.get("run_at")))}</td><td class="mono">{esc(r.get("snapshot_id", ""))}</td>'
            f'<td>{esc(r.get("model", ""))}</td><td class="num">{r.get("drafted", 0)}</td><td class="num">{drafted("agent")}</td>'
            f'<td class="num">{drafted("writeoff")}</td><td class="num">{drafted("escalate")}</td>'
            f'<td class="num">{money(r.get("drafted_dollars", 0))}</td><td class="num">{r.get("errors", 0)}</td>'
            f'<td class="num">{r.get("duration_s", 0)}s</td><td class="num">{r.get("approved", 0)}</td>'
            f'<td class="num">{r.get("rejected", 0)}</td></tr>')
    parts.append('<div class="section"><div class="table-wrap"><table><thead><tr><th class="num">Run at</th><th>Snapshot</th>'
                 '<th>Model</th><th class="num">Drafted</th><th class="num">Agent</th><th class="num">Write off</th>'
                 '<th class="num">Escalate</th><th class="num">Dollars drafted</th><th class="num">Errors</th>'
                 '<th class="num">Duration</th><th class="num">Approved to date</th><th class="num">Rejected to date</th></tr></thead>'
                 f'<tbody>{"".join(body)}</tbody></table></div></div>')
    return page("Runs", "".join(parts), "summary")


# ---------------------------------------------------------------- JSON API

STATE_FILTERS = ("drafted", "edited", "approved", "rejected", "not_drafted", "awaiting")


def api_summary():
    queue = load_queue()
    packages = load_packages()
    live = live_counts()
    buckets = {b: {"queued": 0, "dollars": 0.0, "not_drafted": 0, "drafted": 0, "edited": 0, "approved": 0, "rejected": 0}
               for b, _ in BUCKETS}
    for item in (queue or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        entry = buckets[bucket_of(item)]
        entry["queued"] += 1
        entry["dollars"] += amount_of(item)
        status = packages.get(item_id(item))
        state = status.get("state") if status else "not_drafted"
        if state in entry:
            entry[state] += 1
    for entry in buckets.values():
        entry["dollars"] = round(entry["dollars"], 2)
    runs = read_json(SUMMARY_PATH)
    runs = [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
    last = runs[-1] if runs else None
    return {
        "ok": True,
        "as_of": (queue or {}).get("as_of"),
        "snapshot_id": (queue or {}).get("snapshot_id"),
        "totals": (queue or {}).get("totals") or {},
        "buckets": buckets,
        "review": {"packages": live["packages"], "awaiting_review": live["awaiting"], "drafted": live["drafted"],
                   "edited": live["edited"], "approved": live["approved"], "rejected": live["rejected"],
                   "approved_dollars": round(live["approved_dollars"], 2),
                   "written_off_dollars": round(live["written_off"], 2)},
        "last_run": None if last is None else {k: last.get(k) for k in
                                               ("run_at", "model", "snapshot_id", "drafted", "errors", "duration_s")},
        "runs": len(runs),
    }


def api_queue(query):
    bucket = query.get("bucket", [""])[0]
    state = query.get("state", [""])[0]
    if bucket and bucket not in BUCKET_ACTION:
        return 400, {"ok": False, "error": f"bucket must be one of {', '.join(BUCKET_ACTION)}"}
    if state and state not in STATE_FILTERS:
        return 400, {"ok": False, "error": f"state must be one of {', '.join(STATE_FILTERS)}"}
    try:
        limit = int(query.get("limit", ["50"])[0])
    except ValueError:
        return 400, {"ok": False, "error": "limit must be a number"}
    limit = max(1, min(limit, 5000))
    queue = load_queue()
    if queue is None:
        return 503, {"ok": False, "error": "no queue yet, triage has not run"}
    packages = load_packages()
    rows = []
    for item in queue.get("items") or []:
        if not isinstance(item, dict) or (bucket and bucket_of(item) != bucket):
            continue
        status = packages.get(item_id(item))
        row_state = status.get("state") if status else "not_drafted"
        if state == "awaiting" and row_state not in ("drafted", "edited"):
            continue
        if state and state != "awaiting" and row_state != state:
            continue
        inv = invoice_of(item)
        rows.append({"invoice_id": item_id(item), "bucket": bucket_of(item), "amount": round(amount_of(item), 2),
                     "account_name": inv.get("account_name") or "", "days_past_due": days_past_due(item, queue.get("as_of")),
                     "state": row_state, "action_id": status.get("action_id") if status else None})
    sort = query.get("sort", [""])[0]
    if sort in ("amount", "amount_desc"):
        rows.sort(key=lambda r: r["amount"], reverse=True)
    elif sort == "days_past_due":
        rows.sort(key=lambda r: r["days_past_due"] or 0, reverse=True)
    return 200, {"ok": True, "as_of": queue.get("as_of"), "snapshot_id": queue.get("snapshot_id"),
                 "total": len(rows), "returned": min(len(rows), limit), "rows": rows[:limit]}


def api_invoice(invoice_id):
    if not ID_RE.match(invoice_id):
        return 404, {"ok": False, "error": "not an invoice id"}
    queue = load_queue() or {}
    item = (queue.get("_index") or {}).get(invoice_id)
    folder = ACTIONS_DIR / invoice_id
    status = read_json(folder / "status.json")
    status = status if isinstance(status, dict) else None
    crm_inv, crm_case = crm_lookup(invoice_id)
    if item is None and status is None and crm_inv is None:
        return 404, {"ok": False, "error": f"no invoice {invoice_id} in the queue, out/actions or the CRM"}
    artifacts = {}
    if status is not None:
        for name, _ in ARTIFACTS:
            if (folder / name).exists():
                artifacts[name] = (folder / name).read_text(encoding="utf-8")
    return 200, {
        "ok": True,
        "invoice_id": invoice_id,
        "bucket": bucket_of(item) if item else None,
        "days_past_due": days_past_due(item, queue.get("as_of")) if item else None,
        "current_action_id": current_action_id(item, queue) if item else None,
        "status": status,
        "item": item,
        "artifacts": artifacts,
        "crm_invoice": crm_inv,
        "crm_case": crm_case,
    }


def _drafting_now():
    """True while a drafting step runs on this machine (Linux /proc; False elsewhere)."""
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmd = open(f"/proc/{entry.name}/cmdline", "rb").read().replace(b"\0", b" ")
            except OSError:
                continue
            if b"agent/actions.py" in cmd:
                return True
    except OSError:
        pass
    return False


def _model_name():
    try:
        for line in (ROOT / "platform" / "model.env").read_text().splitlines():
            if line.startswith("MODEL_NAME="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _loaded_models():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.5) as r:
            data = json.load(r)
        return [{"name": m.get("name"), "gb": round((m.get("size") or 0) / 1e9, 1)} for m in data.get("models") or []]
    except Exception:
        return []


def _memory_gb():
    try:
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
        total, avail = info["MemTotal"] / 1048576, info["MemAvailable"] / 1048576
        return {"used": round(total - avail, 1), "total": round(total, 1)}
    except Exception:
        return None


def api_activity():
    """What the background agent is doing right now, for the live strip on every page."""
    now = datetime.now()
    packages = load_packages()
    counts = {"drafted": 0, "edited": 0, "approved": 0, "rejected": 0}
    recent = []
    index = (load_queue() or {}).get("_index") or {}
    for invoice_id, status in packages.items():
        state = status.get("state")
        if state in counts:
            counts[state] += 1
        stamp = status.get("drafted_at") or status.get("at")
        try:
            when = datetime.fromisoformat(str(stamp)[:19])
        except ValueError:
            continue
        recent.append((when, invoice_id, state))
    recent.sort(reverse=True)
    latest = []
    for when, invoice_id, state in recent[:5]:
        item = index.get(invoice_id)
        latest.append({"invoice_id": invoice_id, "state": state, "ago_s": max(0, int((now - when).total_seconds())),
                       "amount": round(amount_of(item), 2) if item else None,
                       "account_name": invoice_of(item).get("account_name") if item else None})
    hour_ago = [w for w, _, _ in recent if (now - w).total_seconds() <= 3600]
    secs_into_cycle = (now.minute % 2) * 60 + now.second
    return {"ok": True, "drafting_now": _drafting_now(), "next_cycle_s": 120 - secs_into_cycle,
            "packages": len(packages), "awaiting": counts["drafted"] + counts["edited"], "approved": counts["approved"],
            "rejected": counts["rejected"], "drafted_last_hour": len(hour_ago), "latest": latest,
            "model": _model_name(), "loaded": _loaded_models(), "memory": _memory_gb()}


def api_post(invoice_id, action, body):
    if not ID_RE.match(invoice_id) or not (ACTIONS_DIR / invoice_id / "status.json").exists():
        return 404, {"ok": False, "error": f"no package for {invoice_id}; the agent has not drafted it"}
    by = (str(body.get("by") or "").strip() or "openclaw")[:60]
    if action in ("approve", "reject"):
        state = "approved" if action == "approve" else "rejected"
        reason = str(body.get("reason") or "").strip()[:500] or None
        status, case_id = set_state(invoice_id, state, by, reason)
        notify(f"{invoice_id} {state} by {by} from chat" + (f": {reason}" if reason else "."))
        return 200, {"ok": True, "invoice_id": invoice_id, "status": status, "crm_case_id": case_id}
    name, content = body.get("file"), body.get("content")
    if not isinstance(name, str) or not isinstance(content, str) or not content.strip():
        return 400, {"ok": False, "error": 'edit needs "file" and a non-empty "content"'}
    try:
        status, case_id = save_artifact(invoice_id, name, content, by)
    except EditError as exc:
        return exc.code, {"ok": False, "error": str(exc)}
    return 200, {"ok": True, "invoice_id": invoice_id, "file": name, "status": status, "crm_case_id": case_id}


# ---------------------------------------------------------------- HTTP

# ---------------------------------------------------------------- the OpenClaw agent in the NemoClaw sandbox

AGENT_LOCK = threading.Lock()
AGENT_JOBS = {}
AGENT_MODEL = os.environ.get("FT_AGENT_MODEL", "gpt-oss:120b")
AGENT_SANDBOX = os.environ.get("FT_AGENT_SANDBOX", "collections")
AGENT_NOISE = ("Using sandbox", "[config] warnings", "Removed retired", "ExperimentalWarning", "trace-warnings")
AGENT_SUGGESTIONS = (
    "How is collections doing today?",
    "What's waiting for approval? Show me the five biggest.",
    "Show me INV-00598.",
    "Approve INV-00598.",
    "Reject INV-05506 because the customer already paid by wire.",
    "Show me the write-off memo for INV-07739.",
)


def openshell_bin():
    found = os.environ.get("FT_OPENSHELL") or shutil.which("openshell")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "openshell"
    return str(fallback) if fallback.exists() else None


def agent_history_path():
    return OUT_DIR / "agent_chat.json"


def agent_history():
    data = read_json(agent_history_path())
    return data if isinstance(data, list) else []


def agent_record(job):
    keep = {k: job.get(k) for k in ("id", "question", "answer", "error", "state", "asked_at", "elapsed_s", "by", "model")}
    history = [keep] + [h for h in agent_history() if h.get("id") != job["id"]]
    try:
        atomic_write(agent_history_path(), json.dumps(history[:50], indent=2, ensure_ascii=False))
    except OSError:
        traceback.print_exc()


INV_IN_TEXT = re.compile(r"\bINV-\d{3,6}\b", re.I)
ITEM_KEEP = ("invoice_id", "bucket", "reason", "reasons", "account_name", "account_owner", "amount", "amount_paid", "balance",
             "status", "dispute_flag", "issue_date", "due_date", "days_past_due", "last_touch_date", "last_touch_source",
             "days_since_touch", "never_touched", "conflict", "sheet_notes", "notes", "slack", "slack_thread", "last_touch")


def _trim(value, limit=700):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " ..."


def agent_context(question):
    """Live desk data handed to the agent with the question, so it answers in one pass and never runs a command."""
    summary = api_summary()
    parts = ["SUMMARY " + json.dumps({k: summary.get(k) for k in ("as_of", "totals", "buckets", "review", "last_run")},
                                     ensure_ascii=False, default=str)]
    for invoice_id in list(dict.fromkeys(m.upper() for m in INV_IN_TEXT.findall(question)))[:3]:
        status, data = api_invoice(invoice_id)
        if status != 200:
            parts.append(f"INVOICE {invoice_id}: not in the queue or the drafts")
            continue
        item = data.get("item") or {}
        inv = invoice_of(item)
        compact = {k: inv.get(k, item.get(k)) for k in ITEM_KEEP if inv.get(k, item.get(k)) not in (None, "", [])}
        compact.update(bucket=data.get("bucket"), days_past_due=data.get("days_past_due"),
                       review_state=(data.get("status") or {}).get("state", "not drafted yet"))
        drafts = {name: _trim(text, 900) for name, text in (data.get("artifacts") or {}).items()}
        parts.append(f"INVOICE {invoice_id} " + _trim(compact, 1500) + (" DRAFTS " + json.dumps(drafts, ensure_ascii=False) if drafts else ""))
    _, waiting = api_queue({"state": ["awaiting"], "sort": ["amount"], "limit": ["10"]})
    if waiting.get("ok"):
        parts.append("TOP 10 AWAITING REVIEW BY AMOUNT " + json.dumps(waiting.get("rows"), ensure_ascii=False, default=str))
    _, oldest = api_queue({"sort": ["days_past_due"], "limit": ["5"]})
    if oldest.get("ok"):
        parts.append("OLDEST 5 QUEUED " + json.dumps(oldest.get("rows"), ensure_ascii=False, default=str))
    return "\n".join(parts)


def agent_prompt(question):
    return ("You are the collections agent for the approval desk. The desk looked up the live data below just now. "
            "Answer only from it, in at most eight short lines or a small table. Use plain numbers with commas and dollars to the cent. "
            "Do not run commands or tools; everything you need is here. If the data does not cover the question, say what is missing. "
            "You cannot approve or reject; tell the collector to type approve INV-... or reject INV-... because ... in this chat, "
            "or press the button on the invoice page.\n\nDATA\n" + agent_context(question) + "\n\nQUESTION\n" + question)


def run_agent_job(job, binary):
    started = time.time()
    cmd = [binary, "sandbox", "exec", "--", "openclaw", "agent",
           "--session-id", f"dash-{job['id']}", "--timeout", "280", "-m", agent_prompt(job["question"])]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=330, stdin=subprocess.DEVNULL)
        lines = [l for l in (proc.stdout or "").splitlines() if not any(n in l for n in AGENT_NOISE)]
        answer = "\n".join(lines).strip()
        if not answer:
            errs = [l for l in (proc.stderr or "").splitlines() if l.strip() and not any(n in l for n in AGENT_NOISE)]
            job.update(state="error", error=(errs[-1] if errs else f"the agent returned nothing (exit {proc.returncode})"))
        elif answer.startswith("⚠"):
            job.update(state="error", error=answer.lstrip("⚠️ ").strip())
        else:
            job.update(state="done", answer=answer)
    except subprocess.TimeoutExpired:
        job.update(state="error", error="the agent took longer than 5 minutes")
    except Exception as exc:  # keep the desk up whatever the sandbox does
        traceback.print_exc()
        job.update(state="error", error=f"could not reach the sandbox: {exc}")
    finally:
        job["elapsed_s"] = round(time.time() - started, 1)
        agent_record(job)
        AGENT_LOCK.release()


def agent_running():
    return next((j for j in AGENT_JOBS.values() if j.get("state") == "running"), None)


DECISION_RE = re.compile(r"^\s*(approve|reject)\s+(INV-\d{3,6})\b[\s,.:;-]*(?:(?:because|reason|as)\s+)?(.*)$", re.I | re.S)


def desk_decision(question, by):
    """A reviewer typing 'approve INV-1' or 'reject INV-1 because ...' in the chat is a human decision.
    The desk records it under the reviewer's name. The sandboxed agent never writes."""
    m = DECISION_RE.match(question)
    if not m:
        return None
    action, invoice_id, reason = m.group(1).lower(), m.group(2).upper(), m.group(3).strip().rstrip(".") or None
    job = {"id": uuid.uuid4().hex[:12], "question": question, "asked_at": now_iso(), "by": by, "model": "desk", "elapsed_s": 0.0}
    status, data = api_post(invoice_id, action, {"by": by, "reason": reason})
    if status == 200:
        verb = "Approved" if action == "approve" else "Rejected"
        case = f" Salesforce case #{data['crm_case_id']} updated." if data.get("crm_case_id") else ""
        why = f' Reason: "{reason}".' if reason and action == "reject" else ""
        job.update(state="done", answer=f"**{verb} {invoice_id}** as {by}.{why}{case} Recorded by the desk under your name; the agent only reads.")
    else:
        job.update(state="error", error=data.get("error") or f"could not {action} {invoice_id}")
    AGENT_JOBS[job["id"]] = job
    agent_record(job)
    return job


def api_agent_ask(body):
    question = str(body.get("message") or "").strip()
    if not question:
        return 400, {"ok": False, "error": "message is required"}
    by = str(body.get("by") or "").strip()[:60] or "reviewer"
    decided = desk_decision(question, by)
    if decided:
        return 200, {"ok": True, "job": decided["id"]}
    if len(question) > 2000:
        return 400, {"ok": False, "error": "keep the question under 2,000 characters"}
    binary = openshell_bin()
    if not binary:
        return 503, {"ok": False, "error": "OpenShell is not installed on this machine, so the sandboxed agent is unreachable"}
    if not AGENT_LOCK.acquire(blocking=False):
        running = agent_running()
        return 409, {"ok": False, "error": "the agent is still answering the last question", "job": running and running["id"]}
    job = {"id": uuid.uuid4().hex[:12], "question": question, "state": "running", "asked_at": now_iso(),
           "by": str(body.get("by") or "dashboard")[:60], "model": AGENT_MODEL, "started": time.time()}
    AGENT_JOBS[job["id"]] = job
    threading.Thread(target=run_agent_job, args=(job, binary), daemon=True).start()
    return 202, {"ok": True, "job": job["id"]}


def api_agent_job(job_id):
    job = AGENT_JOBS.get(job_id) or next((h for h in agent_history() if h.get("id") == job_id), None)
    if not job:
        return 404, {"ok": False, "error": f"no agent job {job_id}"}
    out = {k: job.get(k) for k in ("id", "question", "answer", "error", "state", "asked_at", "elapsed_s", "model")}
    if job.get("state") == "running":
        out["elapsed_s"] = round(time.time() - job.get("started", time.time()), 1)
    return 200, {"ok": True, **out}


def render_agent():
    running = agent_running()
    chips = "".join(f'<button type="button" data-q="{esc(t)}">{esc(t)}</button>' for t in AGENT_SUGGESTIONS)
    items = []
    for h in agent_history()[:20]:
        when = fmt_ts(h.get("asked_at")) if h.get("asked_at") else ""
        secs = f" · {h['elapsed_s']}s" if h.get("elapsed_s") else ""
        inner = (f'<div class="a md">{esc(h.get("answer") or "")}</div>' if h.get("state") == "done"
                 else f'<div class="a err">{esc(h.get("error") or "")}</div>')
        items.append(f'<div class="qa"><div class="q">{esc(h.get("question"))}<span class="when">{esc(when)}{esc(secs)}</span></div>{inner}</div>')
    thread = "".join(items) or '<div class="empty" id="noqa">No questions yet. Try one of the suggestions.</div>'
    body = (
        '<h1>Ask the agent</h1>'
        f'<p class="sub">OpenClaw answers from the same queue this desk shows. It can approve or reject an invoice you name.</p>'
        f'<div class="sandbox-note">NemoClaw sandbox <b>{esc(AGENT_SANDBOX)}</b> · OpenClaw · {esc(AGENT_MODEL)} on this GB10 · no cloud calls</div>'
        '<div class="section"><div class="ask">'
        f'<div class="suggest">{chips}</div>'
        '<textarea id="agent-q" rows="3" placeholder="How is collections doing today?"></textarea>'
        '<div class="ask-row"><button class="btn primary" id="agent-ask" type="button">Ask</button>'
        '<span class="ask-state" id="agent-state"></span></div>'
        '</div></div>'
        f'<div class="section" id="agent-thread">{thread}</div>'
        f'<script>window.AGENT_RUNNING = {json.dumps(running["id"] if running else None)};</script>'
        f'<script>{AGENT_JS}</script>'
    )
    return page("Ask the agent", body, "agent")


def agent_suggestions():
    """Suggestion chips that name real invoices from the live queue, so they work on any data set."""
    try:
        _, top = api_queue({"state": ["awaiting"], "sort": ["amount"], "limit": ["1"]})
        _, wo = api_queue({"bucket": ["writeoff"], "state": ["awaiting"], "limit": ["1"]})
        if not (wo.get("rows") or []):
            _, wo = api_queue({"bucket": ["writeoff"], "limit": ["1"]})
        _, esc_rows = api_queue({"bucket": ["escalate"], "limit": ["1"]})
    except Exception:
        return AGENT_SUGGESTIONS
    first = lambda d: ((d.get("rows") or [{}])[0]).get("invoice_id") if d.get("ok") else None
    big, small, disputed = first(top), first(wo), first(esc_rows)
    out = ["How is collections doing today?", "What's waiting for approval? Show me the five biggest."]
    if big:
        out += [f"Show me {big}. What did the agent draft?", f"Approve {big}."]
    if disputed:
        out.append(f"Why is {disputed} escalated?")
    if small:
        out.append(f"Should we write off {small}?")
    return tuple(out)


def agent_panel():
    chips = "".join(f'<button type="button" data-q="{esc(t)}">{esc(t)}</button>' for t in agent_suggestions())
    return (
        '<button class="agent-fab" id="agent-fab" type="button" aria-controls="agent-panel" aria-expanded="false">'
        '<img class="collex-face" src="/assets/collex-companion.png" alt="" width="42" height="42">Ask Collex</button>'
        '<section class="agent-panel" id="agent-panel" aria-label="Ask the agent">'
        '<div class="ap-head"><div class="t"><img class="collex-face" src="/assets/collex-companion.png" alt="" width="68" height="68">Collex<button class="x" id="agent-close" type="button" aria-label="Close">×</button></div>'
        f'<div class="n">OpenClaw in NemoClaw sandbox <b>{esc(AGENT_SANDBOX)}</b> · {esc(AGENT_MODEL)} on this GB10 · no cloud calls</div></div>'
        '<div class="ap-body" id="agent-body"></div>'
        f'<div class="ap-suggest" id="agent-suggest">{chips}</div>'
        '<div class="ap-ctx" id="agent-ctx"><b>Asking about this selection</b><span id="agent-ctx-text"></span>'
        '<button class="rm" id="agent-ctx-rm" type="button" aria-label="Remove selection">\u00d7</button></div>'
        '<div class="ap-input"><textarea id="agent-q" rows="1" placeholder="Ask about the queue, or approve an invoice"></textarea>'
        '<button class="btn primary" id="agent-ask" type="button">Ask</button></div>'
        '</section>'
        '<button class="ap-select" id="agent-select" type="button">Ask about this</button>'
        '<div class="agent-live" id="agent-live" title="Click for the latest drafts" role="status" aria-live="polite">'
        '<div class="al-top"><span class="al-dot"></span><span id="al-state">Local agent</span></div>'
        '<div class="al-row"><span class="al-num" id="al-num">\u2013</span><span class="al-sub">drafts</span>'
        '<span class="al-plus" id="al-plus"></span></div>'
        '<div class="al-sub" id="al-latest"></div><div class="al-sub" id="al-model"></div>'
        '<div class="al-list" id="al-list"></div></div>'
    )


WIDGET_CSS = (
    "#agent-panel,.agent-fab,.ap-select{--accent:#1e5c3a;--accent-hover:#174a2e;--accent-soft:#e8f2ec;--accent-line:#bcd9c7;"
    "--accent-ink:#174a2e;--panel:#fff;--line:#e3e6ea;--line-strong:#cdd2d9;--text:#1c2128;--muted:#5b6470;--faint:#8b939d;--chip:#eff1f4;"
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;box-sizing:border-box}"
    "#agent-panel *{box-sizing:border-box}"
    "#agent-panel .btn{font:inherit;font-weight:550;padding:7px 14px;border-radius:6px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}"
    "#agent-panel .btn:hover{background:var(--accent-hover)}#agent-panel .btn:disabled{opacity:.6;cursor:default}"
    "#agent-panel textarea{display:block;width:100%;color:var(--text);border:1px solid var(--line-strong);border-radius:6px}"
    "#agent-panel code{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:var(--chip);padding:1px 4px;border-radius:4px}"
    "#agent-panel a{color:var(--accent)}"
)


def agent_widget_js():
    kf = "@keyframes pulse{50%{opacity:.35}}"
    base = CSS.replace(kf, "")
    css = WIDGET_CSS + kf + "".join(rule + "}" for rule in base.split("}") if any(k in rule for k in (".agent-", ".ap-", ".collex-face", "--selection", "#asktoggle")))
    return ("(function(){if(document.getElementById('agent-panel'))return;"
            f"var st=document.createElement('style');st.textContent={json.dumps(css)};document.head.appendChild(st);"
            f"document.body.insertAdjacentHTML('beforeend',{json.dumps(agent_panel())});"
            "})();\n" + AGENT_PANEL_JS)


AGENT_PANEL_JS = r"""
(function () {
  var fab = document.getElementById("agent-fab"), panel = document.getElementById("agent-panel");
  if (!fab || !panel) { return; }
  var body = document.getElementById("agent-body"), q = document.getElementById("agent-q"), btn = document.getElementById("agent-ask");
  var KEY = "collections.agent.open", loaded = false, polling = null;
  function store(k, v) { try { if (v === null) { localStorage.removeItem(k); } else { localStorage.setItem(k, v); } } catch (e) {} }
  function read(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  function md(src) {
    var out = [], list = false;
    String(src).split(/\n/).forEach(function (raw) {
      var line = esc(raw).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/\b(INV-\d{4,6})\b/g, '<a href="/invoice/$1">$1</a>');
      var m = line.match(/^\s*[-*•]\s+(.*)$/);
      if (m) { if (!list) { out.push("<ul>"); list = true; } out.push("<li>" + m[1] + "</li>"); return; }
      if (list) { out.push("</ul>"); list = false; }
      line = line.replace(/^#{1,4}\s+(.*)$/, "<b>$1</b>");
      if (line.trim()) { out.push("<p>" + line + "</p>"); }
    });
    if (list) { out.push("</ul>"); }
    return out.join("");
  }
  function bubble(cls, html) { var d = document.createElement("div"); d.className = "ap-msg " + cls; d.innerHTML = html; body.appendChild(d); body.scrollTop = body.scrollHeight; return d; }
  function answer(j, into) {
    var el = into || bubble("bot", "");
    if (j.state === "done") { el.className = "ap-msg bot"; el.innerHTML = '<img class="collex-face" src="/assets/collex-companion.png" alt="" width="38" height="38">' + md(j.answer) + '<span class="meta">' + (j.elapsed_s ? j.elapsed_s + "s" : "") + "</span>"; }
    else { el.className = "ap-msg bot err"; el.textContent = j.error || "The agent could not answer."; }
    if (j.state === "done" && /\b(approve|reject|edit)\b/i.test(j.question || "")) {
      var r = document.createElement("a"); r.href = location.pathname + location.search; r.className = "ap-refresh"; r.textContent = "Refresh this page to see the change";
      body.appendChild(r);
    }
    body.scrollTop = body.scrollHeight;
  }
  function setBusy(on) { btn.disabled = on; fab.classList.toggle("busy", on); }
  function poll(id, el) {
    store("collections.agent.job", id);
    setBusy(true);
    fetch("/api/agent/job/" + id).then(function (r) { return r.json(); }).then(function (j) {
      if (j.state === "running") {
        el.textContent = "Working in the sandbox · " + Math.round(j.elapsed_s || 0) + "s";
        polling = setTimeout(function () { poll(id, el); }, 2000); return;
      }
      store("collections.agent.job", null); setBusy(false); answer(j, el);
    }).catch(function () { polling = setTimeout(function () { poll(id, el); }, 3000); });
  }
  function load() {
    if (loaded) { return; } loaded = true;
    fetch("/api/agent/history").then(function (r) { return r.json(); }).then(function (h) {
      var items = (h.history || []).slice(0, 8).reverse();
      if (!items.length) { bubble("bot", "<p>Ask about the queue, open an invoice, or approve or reject one by number. Answers take about half a minute.</p>"); }
      items.forEach(function (it) {
        if (it.state === "running") { return; }
        bubble("me", esc(it.question)); answer(it);
      });
      var running = h.running || read("collections.agent.job");
      if (running) { fetch("/api/agent/job/" + running).then(function (r) { return r.json(); }).then(function (j) {
        if (j.ok && j.state === "running") { bubble("me", esc(j.question)); poll(running, bubble("bot work", "Working in the sandbox")); }
        else { store("collections.agent.job", null); }
      }); }
    }).catch(function () { bubble("bot err", "Could not load the conversation."); });
  }
  function open(on) {
    panel.classList.toggle("open", on); fab.setAttribute("aria-expanded", on ? "true" : "false");
    store(KEY, on ? "1" : null);
    if (on) { load(); setTimeout(function () { q.focus(); }, 50); }
  }
  fab.addEventListener("click", function () { open(!panel.classList.contains("open")); });
  document.getElementById("agent-close").addEventListener("click", function () { open(false); });
  document.querySelectorAll("#agent-suggest button").forEach(function (b) { b.addEventListener("click", function () { q.value = b.getAttribute("data-q"); q.focus(); }); });
  var ctx = null, selBtn = document.getElementById("agent-select"), ctxBox = document.getElementById("agent-ctx");
  function pageInvoice() { var m = location.pathname.match(/\/invoice\/(INV-\d+)/); return m ? m[1] : null; }
  function setCtx(c) {
    ctx = c; ctxBox.classList.toggle("on", !!c);
    document.getElementById("agent-ctx-text").textContent = c ? "\u201c" + (c.text.length > 160 ? c.text.slice(0, 160) + "\u2026" : c.text) + "\u201d" : "";
    q.placeholder = c ? "What do you want to know about this?" : "Ask about the queue, or approve an invoice";
  }
  document.getElementById("agent-ctx-rm").addEventListener("click", function () { setCtx(null); });
  function hideSel() { selBtn.style.display = "none"; }
  document.addEventListener("mouseup", function (e) {
    if (panel.contains(e.target) || e.target === selBtn || e.target === fab) { return; }
    setTimeout(function () {
      var sel = window.getSelection(), text = sel ? sel.toString().trim() : "";
      if (text.length < 2 || !sel.rangeCount) { hideSel(); return; }
      var r = sel.getRangeAt(0).getBoundingClientRect();
      selBtn.style.left = Math.min(window.scrollX + r.right - 40, window.scrollX + document.documentElement.clientWidth - 140) + "px";
      selBtn.style.top = (window.scrollY + r.bottom + 6) + "px";
      selBtn.style.display = "block";
      selBtn.dataset.text = text.slice(0, 1200);
    }, 0);
  });
  selBtn.addEventListener("mousedown", function (e) { e.preventDefault(); });
  selBtn.addEventListener("click", function () {
    setCtx({text: selBtn.dataset.text || "", path: location.pathname, invoice: pageInvoice()});
    hideSel(); open(true); if (window.getSelection) { window.getSelection().removeAllRanges(); }
  });
  document.addEventListener("scroll", hideSel, true);
  function send() {
    var typed = q.value.trim(); if ((!typed && !ctx) || btn.disabled) { return; }
    var text = typed, shown = esc(typed);
    if (ctx) {
      var ask = typed || "Explain this and tell me what, if anything, the collector should do about it.";
      text = "The collector selected this text on the dashboard page " + ctx.path + (ctx.invoice ? " (invoice " + ctx.invoice + ")" : "") +
        ":\n\"\"\"\n" + ctx.text + "\n\"\"\"\nTheir question: " + ask +
        "\nUse the collections skill to look up any invoice it mentions before answering.";
      shown = '<span class="quote">' + esc(ctx.text.length > 200 ? ctx.text.slice(0, 200) + "\u2026" : ctx.text) + "</span>" + esc(ask);
      setCtx(null);
    }
    var who = document.getElementById("reviewer");
    bubble("me", shown); q.value = "";
    var el = bubble("bot work", "Sending to the sandbox");
    setBusy(true);
    fetch("/api/agent/ask", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, by: (who && who.value.trim()) || "dashboard"})})
      .then(function (r) { return r.json().then(function (j) { return {status: r.status, j: j}; }); })
      .then(function (res) {
        if (res.j.ok) { poll(res.j.job, el); }
        else if (res.status === 409 && res.j.job) { el.textContent = "Still answering the last question"; poll(res.j.job, el); }
        else { setBusy(false); el.className = "ap-msg bot err"; el.textContent = res.j.error || "Could not send"; }
      }).catch(function () { setBusy(false); el.className = "ap-msg bot err"; el.textContent = "Could not reach the desk"; });
  }
  btn.addEventListener("click", send);
  q.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

  (function live() {
    var box = document.getElementById("agent-live"); if (!box) { return; }
    var last = null, shown = null, anim = null;
    function ago(s) { return s < 60 ? s + "s ago" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; }
    function money(x) { return x == null ? "" : "$" + Number(x).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
    function countTo(n) {
      var el = document.getElementById("al-num"); if (shown === null) { shown = n; el.textContent = n.toLocaleString(); return; }
      if (n === shown) { return; }
      var plus = document.getElementById("al-plus"); if (n > shown) { plus.textContent = "+" + (n - shown); plus.classList.add("on"); el.classList.add("bump");
        setTimeout(function () { plus.classList.remove("on"); el.classList.remove("bump"); }, 2500); }
      clearInterval(anim); var from = shown; shown = n; var step = 0;
      anim = setInterval(function () { step++; var v = Math.round(from + (n - from) * step / 12); el.textContent = v.toLocaleString(); if (step >= 12) { clearInterval(anim); } }, 40);
    }
    box.addEventListener("click", function () { box.classList.toggle("expanded"); });
    function tick() {
      fetch("/api/agent/activity").then(function (r) { return r.json(); }).then(function (a) {
        if (!a.ok) { return; }
        box.classList.toggle("busy", !!a.drafting_now);
        document.getElementById("al-state").textContent = a.drafting_now ? "Local agent is drafting now" : "Local agent idle · next cycle in " + a.next_cycle_s + "s";
        countTo(a.packages);
        var l = (a.latest || [])[0];
        document.getElementById("al-latest").textContent = (a.awaiting || 0).toLocaleString() + " awaiting review · " + (a.drafted_last_hour || 0) + " drafted in the last hour" +
          (l ? " · latest " + l.invoice_id + " " + ago(l.ago_s) : "");
        var models = (a.loaded || []).map(function (m) { return m.name.replace(":latest", ""); }).join(", ");
        document.getElementById("al-model").textContent = (models || a.model || "") + (a.memory ? " · " + a.memory.used + " of " + a.memory.total + " GB in use" : "");
        document.getElementById("al-list").innerHTML = (a.latest || []).map(function (x) {
          return "<div><span>" + esc(x.invoice_id) + " " + esc(x.account_name || "") + " " + money(x.amount) + "</span><span>" + esc(x.state) + " · " + ago(x.ago_s) + "</span></div>";
        }).join("");
      }).catch(function () {}).then(function () { setTimeout(tick, 3000); });
    }
    tick();
  })();
  if (read(KEY) === "1" || /[?&]ask=1\b/.test(location.search)) { open(true); }
  else if (read("collections.agent.job")) { setBusy(true); open(true); }
})();
"""


AGENT_JS = r"""
(function () {
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  function md(src) {
    var out = [], list = false;
    String(src).split(/\n/).forEach(function (raw) {
      var line = esc(raw).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
      var m = line.match(/^\s*[-*•]\s+(.*)$/);
      if (m) { if (!list) { out.push("<ul>"); list = true; } out.push("<li>" + m[1] + "</li>"); return; }
      if (list) { out.push("</ul>"); list = false; }
      line = line.replace(/^#{1,4}\s+(.*)$/, "<b>$1</b>");
      if (line.trim()) { out.push("<p>" + line + "</p>"); }
    });
    if (list) { out.push("</ul>"); }
    return out.join("");
  }
  document.querySelectorAll(".md").forEach(function (el) { el.innerHTML = md(el.textContent); el.classList.remove("md"); });
  var q = document.getElementById("agent-q"), btn = document.getElementById("agent-ask"), state = document.getElementById("agent-state");
  var thread = document.getElementById("agent-thread"), timer = null;
  document.querySelectorAll(".suggest button").forEach(function (b) { b.addEventListener("click", function () { q.value = b.getAttribute("data-q"); q.focus(); }); });
  function busy(on, text) { btn.disabled = on; btn.textContent = on ? "Working" : "Ask"; state.textContent = text || ""; }
  function add(question, html) {
    var empty = document.getElementById("noqa"); if (empty) { empty.remove(); }
    var div = document.createElement("div"); div.className = "qa";
    div.innerHTML = '<div class="q">' + esc(question) + '</div>' + html;
    thread.insertBefore(div, thread.firstChild);
  }
  function poll(id) {
    fetch("/api/agent/job/" + id).then(function (r) { return r.json(); }).then(function (j) {
      if (j.state === "running") { busy(true, "OpenClaw is working in the sandbox · " + Math.round(j.elapsed_s) + "s"); timer = setTimeout(function () { poll(id); }, 2000); return; }
      busy(false, j.elapsed_s ? "Answered in " + j.elapsed_s + "s" : "");
      add(j.question, j.state === "done" ? '<div class="a">' + md(j.answer) + "</div>" : '<div class="a err">' + esc(j.error || "failed") + "</div>");
    }).catch(function () { timer = setTimeout(function () { poll(id); }, 3000); });
  }
  btn.addEventListener("click", function () {
    var text = q.value.trim(); if (!text) { q.focus(); return; }
    var who = document.getElementById("reviewer"); 
    busy(true, "Sending to the sandbox");
    fetch("/api/agent/ask", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, by: (who && who.value.trim()) || "dashboard"})})
      .then(function (r) { return r.json().then(function (j) { return {status: r.status, j: j}; }); })
      .then(function (res) {
        if (res.j.ok) { q.value = ""; poll(res.j.job); }
        else if (res.status === 409 && res.j.job) { poll(res.j.job); }
        else { busy(false, res.j.error || "Could not send"); }
      }).catch(function () { busy(false, "Could not reach the desk"); });
  });
  q.addEventListener("keydown", function (e) { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { btn.click(); } });
  if (window.AGENT_RUNNING) { poll(window.AGENT_RUNNING); }
})();
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CollectionsDashboard/1.0"

    def send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200, pretty=False):
        body = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path, query = parts.path, urllib.parse.parse_qs(parts.query)
        if path == "/assets/collex-companion.png":
            here = Path(__file__).resolve().parent
            asset = next((c for c in (here / "assets" / "collex-companion.png", here.parent / "app" / "assets" / "collex-companion.png") if c.exists()), None)
            if asset is None:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            data = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/api/"):
            pretty = query.get("pretty", [""])[0] in ("1", "true")
            try:
                if path == "/api/summary":
                    status, data = 200, api_summary()
                elif path == "/api/queue":
                    status, data = api_queue(query)
                elif path.startswith("/api/invoice/") and path.count("/") == 3:
                    status, data = api_invoice(urllib.parse.unquote(path[len("/api/invoice/"):]))
                elif path.startswith("/api/agent/job/"):
                    status, data = api_agent_job(path[len("/api/agent/job/"):])
                elif path == "/api/agent/activity":
                    status, data = 200, api_activity()
                elif path == "/api/agent/history":
                    status, data = 200, {"ok": True, "history": agent_history()[:20], "running": (agent_running() or {}).get("id")}
                else:
                    status, data = 404, {"ok": False, "error": f"no GET route at {path}"}
            except Exception:
                traceback.print_exc()
                status, data = 500, {"ok": False, "error": "internal error, see the dashboard log"}
            self.send_json(data, status, pretty)
            return
        try:
            if path == "/":
                self.send_html(render_queue(query))
            elif path == "/agent":
                self.redirect("/?ask=1")
            elif path == "/agent-widget.js":
                data = agent_widget_js().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/cases":
                self.send_html(render_cases())
            elif path == "/summary":
                self.send_html(render_summary())
            elif path.startswith("/invoice/"):
                invoice_id = urllib.parse.unquote(path[len("/invoice/"):]).strip("/")
                if not ID_RE.match(invoice_id):
                    self.send_html(not_found("That is not an invoice id."), 404)
                    return
                body, status = render_invoice(invoice_id, query)
                self.send_html(body, status)
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_html(not_found(f"No page at {path}."), 404)
        except Exception:
            traceback.print_exc()
            self.send_html(page("Error", "<h1>Something broke</h1><p class=sub>The traceback is in the dashboard log.</p>"), 500)

    def handle_api_post(self, path):
        m = re.fullmatch(r"/api/invoice/([^/]+)/(approve|reject|edit)", path)
        if not m and path != "/api/agent/ask":
            self.send_json({"ok": False, "error": f"no POST route at {path}"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.send_json({"ok": False, "error": "body too large"}, 413)
            return
        raw = self.rfile.read(length).decode("utf-8", "replace").strip()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            self.send_json({"ok": False, "error": "body must be JSON"}, 400)
            return
        if not isinstance(body, dict):
            self.send_json({"ok": False, "error": "body must be a JSON object"}, 400)
            return
        try:
            if path == "/api/agent/ask":
                status, data = api_agent_ask(body)
            else:
                status, data = api_post(urllib.parse.unquote(m.group(1)), m.group(2), body)
        except Exception:
            traceback.print_exc()
            status, data = 500, {"ok": False, "error": "internal error, see the dashboard log"}
        self.send_json(data, status)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/"):
            self.handle_api_post(path)
            return
        m = re.fullmatch(r"/invoice/([^/]+)/(approve|reject|save)", path)
        if not m:
            self.send_html(not_found(f"No action at {path}."), 404)
            return
        invoice_id, action = urllib.parse.unquote(m.group(1)), m.group(2)
        if not ID_RE.match(invoice_id) or not (ACTIONS_DIR / invoice_id / "status.json").exists():
            self.send_html(not_found(f"No package for {invoice_id}."), 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.send_html(page("Too large", "<h1>That edit is too large</h1>"), 413)
            return
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        by = (form.get("by", [""])[0].strip() or "dashboard")[:60]
        try:
            if action in ("approve", "reject"):
                state = "approved" if action == "approve" else "rejected"
                set_state(invoice_id, state, by)
                notify(f"{invoice_id} {state} by {by} in the dashboard.")
                self.redirect(f"/invoice/{q(invoice_id)}?done={state}")
                return
            name = form.get("file", [""])[0]
            content = form.get("content", [""])[0]
            try:
                save_artifact(invoice_id, name, content, by)
            except EditError as exc:
                if exc.code == 404:
                    self.send_html(not_found(f"{exc}."), 404)
                else:
                    body, _ = render_invoice(invoice_id, {}, error=f"{exc}.", drafts={name: content.replace("\r\n", "\n")})
                    self.send_html(body, 400)
                return
            self.redirect(f"/invoice/{q(invoice_id)}?done=edited")
        except Exception:
            traceback.print_exc()
            self.send_html(page("Error", "<h1>Something broke</h1><p class=sub>The traceback is in the dashboard log.</p>"), 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup (socket.getfqdn). With the uplink pulled
        # that can stall startup for half a minute, so bind without it.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def main(argv=None):
    global QUEUE_PATH
    p = argparse.ArgumentParser(description="Collections approval dashboard (stdlib, works offline).")
    p.add_argument("--host", default="127.0.0.1", help="bind address; 0.0.0.0 to reach it from another machine")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--queue", default=str(QUEUE_PATH), help="queue file (default out/queue.json)")
    args = p.parse_args(argv)
    queue = Path(args.queue)
    if not queue.is_absolute() and not queue.exists() and (ROOT / queue).exists():
        queue = ROOT / queue
    QUEUE_PATH = queue
    server = Server((args.host, args.port), Handler)
    print(f"dashboard on http://{args.host}:{args.port} (queue {display(QUEUE_PATH)})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
