"""Uploaded export bundles: save them, merge them into data/inbox/, rerun the rules, and report what the rules did.

Every upload is kept in data/uploads/<upload_id>/. The inbox is rebuilt as the union of all uploads (later rows win),
so several batches can be dropped one after another. A bundle whose manifest says "kind": "full" replaces the
exports instead of adding to them. The report follows the uploaded invoices through the same filters triage applies.
"""
import csv
import io
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime

import store

sys.path.insert(0, str(store.ROOT / "triage"))
import triage  # noqa: E402

UPLOADS = store.ROOT / "data" / "uploads"
INBOX = store.ROOT / "data" / "inbox"
EXPORTS = store.ROOT / "data" / "exports"
REPORTS = store.OUT / "uploads"
FILES = ("salesforce_invoices.csv", "collector_sheet.csv", "slack_threads.json", "manifest.json")
SF_REQUIRED = ("invoice_id", "account_id", "account_name", "account_owner", "owner_email", "billing_contact_email",
               "currency", "amount", "amount_paid", "status", "issue_date", "due_date", "last_touch_date", "dispute_flag")
SHEET_REQUIRED = ("invoice_id", "collector", "touch_date", "note")
PIPELINE = (["triage/triage.py"], ["triage/revalidate.py"], ["disputes/disputes.py"])


class UploadError(ValueError):
    pass


def check(files):
    """Validate {filename: text}; returns the parsed manifest."""
    unknown = sorted(set(files) - set(FILES))
    if unknown:
        raise UploadError(f"Unexpected file {', '.join(unknown)}. Upload {', '.join(FILES)}.")
    if "salesforce_invoices.csv" not in files:
        raise UploadError("salesforce_invoices.csv is required.")
    for name, required in (("salesforce_invoices.csv", SF_REQUIRED), ("collector_sheet.csv", SHEET_REQUIRED)):
        if name in files:
            header = next(csv.reader(io.StringIO(files[name].lstrip("﻿"))), [])
            missing = [c for c in required if c not in header]
            if missing:
                raise UploadError(f"{name} is missing columns: {', '.join(missing)}.")
    try:
        slack = json.loads(files.get("slack_threads.json", "[]"))
        manifest = json.loads(files.get("manifest.json", "{}"))
    except json.JSONDecodeError as e:
        raise UploadError(f"A JSON file could not be read: {e}") from None
    if not isinstance(slack, list) or not isinstance(manifest, dict):
        raise UploadError("slack_threads.json must be a list and manifest.json an object.")
    return manifest


def rows(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def rebuild_inbox():
    """Write the union of every upload into data/inbox/; returns the merged manifest, or None if there are none."""
    for path in INBOX.iterdir():
        if not path.name.startswith("."):
            path.unlink()
    folders = sorted(p for p in UPLOADS.iterdir() if p.is_dir()) if UPLOADS.exists() else []
    if not folders:
        return None
    invoices, notes, slack, kind, as_of = {}, [], {}, "delta", None
    for folder in folders:
        manifest = store.read_json(folder / "manifest.json", {})
        if manifest.get("kind") == "full":
            invoices, notes, slack, kind = {}, [], {}, "full"
        for r in rows((folder / "salesforce_invoices.csv").read_text(encoding="utf-8")):
            invoices[r["invoice_id"]] = r
        if (folder / "collector_sheet.csv").exists():
            notes += rows((folder / "collector_sheet.csv").read_text(encoding="utf-8"))
        for thread in store.read_json(folder / "slack_threads.json", []):
            slack[thread["invoice_id"]] = thread
        as_of = manifest.get("as_of") or as_of
    with open(INBOX / "salesforce_invoices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SF_REQUIRED, extrasaction="ignore")
        w.writeheader()
        w.writerows(invoices.values())
    with open(INBOX / "collector_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_REQUIRED, extrasaction="ignore")
        w.writeheader()
        w.writerows(notes)
    (INBOX / "slack_threads.json").write_text(json.dumps(list(slack.values()), indent=1), encoding="utf-8")
    manifest = {"kind": kind, "as_of": as_of or store.read_json(EXPORTS / "manifest.json", {}).get("as_of"),
                "uploads": [f.name for f in folders]}
    (INBOX / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def run_pipeline():
    lines = []
    for args in PIPELINE:
        proc = subprocess.run([sys.executable, *args], cwd=store.ROOT, capture_output=True, text=True, timeout=300)
        out = (proc.stdout + proc.stderr).strip().splitlines()
        lines.append(out[0] if out else "")
        if proc.returncode and args == PIPELINE[0]:
            raise UploadError(f"Triage failed on this upload: {out[-1] if out else proc.returncode}")
    return lines


def funnel(upload_ids):
    """Follow the uploaded invoices through triage's filters, in triage's order."""
    policy = triage.load_policy(store.ROOT / "policy.yaml")
    invoices, notes, slack, as_of = triage.load_sources(triage.pick_sources(INBOX, EXPORTS))
    sheet = {}
    for n in notes:
        d = triage.parse_date(n.get("touch_date"))
        if d and (n["invoice_id"] not in sheet or d > sheet[n["invoice_id"]]):
            sheet[n["invoice_id"]] = d

    def facts(inv):
        thread = slack.get(inv["invoice_id"])
        touches = [triage.parse_date(inv.get("last_touch_date")), sheet.get(inv["invoice_id"]),
                   triage.parse_date(thread.get("last_message_date")) if thread else None]
        touches = [t for t in touches if t]
        return {"status": (inv.get("status") or "").strip().lower(),
                "balance": round(float(inv["amount"]) - float(inv["amount_paid"] or 0), 2),
                "dpd": (as_of - triage.parse_date(inv["due_date"])).days,
                "since": (as_of - max(touches)).days if touches else None}

    remaining = [facts(invoices[i]) for i in upload_ids if i in invoices]
    received = len(remaining)
    steps = []
    rules = [
        ("Already paid", lambda f: f["status"] != "paid"),
        (f"Less than {policy['min_days_past_due']} days past due", lambda f: f["dpd"] >= policy["min_days_past_due"]),
        (f"Touched in the last {policy['stale_touch_days']} days",
         lambda f: f["since"] is None or f["since"] >= policy["stale_touch_days"]),
        (f"Balance over {store.money(policy['agent_max_amount'])}, left with collectors",
         lambda f: f["balance"] <= policy["agent_max_amount"]),
        ("No balance left (a data problem, not a write-off)", lambda f: f["balance"] > 0),
    ]
    for label, keep in rules:
        kept = [f for f in remaining if keep(f)]
        steps.append({"label": label, "removed": len(remaining) - len(kept),
                      "dollars": round(sum(f["balance"] for f in remaining if not keep(f)), 2)})
        remaining = kept
    return {"received": received, "steps": steps, "passed": len(remaining), "as_of": as_of.isoformat()}


def save(files, name):
    """Store, merge and process one upload; returns its report."""
    check(files)
    before = store.read_json(store.QUEUE, {"items": [], "totals": {}})
    upload_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = UPLOADS / upload_id
    folder.mkdir(parents=True)
    for filename, text in files.items():
        (folder / filename).write_text(text, encoding="utf-8")
    rebuild_inbox()
    log = run_pipeline()
    after = store.read_json(store.QUEUE, {"items": [], "totals": {}})
    upload_ids = [r["invoice_id"] for r in rows(files["salesforce_invoices.csv"]) if r.get("invoice_id")]
    uploaded = set(upload_ids)
    queued = [i for i in after["items"] if i["invoice_id"] in uploaded]
    exposure = store.account_exposure(after["items"])
    report = {
        "upload_id": upload_id,
        "name": name or upload_id,
        "uploaded_at": store.now(),
        "files": sorted(files),
        "funnel": funnel(upload_ids),
        "queued_ids": [i["invoice_id"] for i in queued],
        "buckets": {b: {"count": sum(i["bucket"] == b for i in queued),
                        "dollars": round(sum(i["balance"] for i in queued if i["bucket"] == b), 2)}
                    for b in ("agent", "writeoff", "escalate")},
        "blockers": dict(Counter(store.BLOCKER_LABEL[k] for k in map(store.blocker, queued) if k)),
        "accounts": len({i["account_id"] for i in queued}),
        "accounts_with_more": sum(1 for a in {i["account_id"] for i in queued} if exposure.get(a, (0,))[0] > 1),
        "queue_before": before["totals"].get("queued", 0),
        "queue_after": after["totals"].get("queued", 0),
        "dollars_before": before["totals"].get("queued_dollars", 0.0),
        "dollars_after": after["totals"].get("queued_dollars", 0.0),
        "minutes_per_touch": after.get("policy", {}).get("minutes_per_touch", 6),
        "pipeline": log,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    store.write_json(REPORTS / f"{upload_id}.json", report)
    return report


def reports():
    if not REPORTS.exists():
        return []
    return sorted((store.read_json(p) for p in REPORTS.glob("*.json")), key=lambda r: r["upload_id"], reverse=True)


def reset():
    """Remove every upload and put the queue back to the base exports."""
    shutil.rmtree(UPLOADS, ignore_errors=True)
    shutil.rmtree(REPORTS, ignore_errors=True)
    rebuild_inbox()
    return run_pipeline()
