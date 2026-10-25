"""Deterministic triage: which small invoices has everyone forgotten about?

python triage/triage.py

Reads data/inbox/ (falls back to data/exports/ when the inbox is empty). A bundle whose manifest says
"kind": "delta" is laid over data/exports/ instead of replacing it. Writes out/queue.json and out/headline.json.
"""
import argparse
import csv
import json
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = ("min_days_past_due", "stale_touch_days", "agent_max_amount", "write_off_max_amount")


def load_policy(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml
        raw = yaml.safe_load(text) or {}
    except ImportError:
        raw = {}
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                key, value = line.split(":", 1)
                raw[key.strip()] = value.strip()
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise SystemExit(f"{path}: missing policy keys {', '.join(missing)}")
    return {
        "min_days_past_due": int(raw["min_days_past_due"]),
        "stale_touch_days": int(raw["stale_touch_days"]),
        "agent_max_amount": float(raw["agent_max_amount"]),
        "write_off_max_amount": float(raw["write_off_max_amount"]),
        "minutes_per_touch": float(raw.get("minutes_per_touch", 6)),
    }


def parse_date(s):
    return date.fromisoformat(s) if s else None


def load_bundle(folder):
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    # utf-8-sig so CSVs saved from Excel or Sheets (with a BOM) still have a clean first header.
    with open(folder / "salesforce_invoices.csv", newline="", encoding="utf-8-sig") as f:
        invoices = {row["invoice_id"]: row for row in csv.DictReader(f) if row.get("invoice_id")}
    notes = []
    if (folder / "collector_sheet.csv").exists():
        with open(folder / "collector_sheet.csv", newline="", encoding="utf-8-sig") as f:
            notes = list(csv.DictReader(f))
    slack = {}
    if (folder / "slack_threads.json").exists():
        slack = {t["invoice_id"]: t for t in json.loads((folder / "slack_threads.json").read_text(encoding="utf-8"))}
    return manifest, invoices, notes, slack


def pick_sources(inbox, exports):
    if not (inbox / "salesforce_invoices.csv").exists():
        if inbox.exists() and any(not p.name.startswith(".") for p in inbox.iterdir()):
            print(f"warning: {inbox} has files but no salesforce_invoices.csv at its top level; using {exports}")
        return [exports]
    manifest_path = inbox / "manifest.json"
    if not manifest_path.exists():
        print(f"warning: {inbox} has no manifest.json; treating it as a full export")
        return [inbox]
    if json.loads(manifest_path.read_text(encoding="utf-8")).get("kind") == "delta":
        return [exports, inbox]
    return [inbox]


def load_sources(folders):
    invoices, notes, slack, as_of = {}, [], {}, None
    for folder in folders:
        manifest, inv, nts, slk = load_bundle(folder)
        invoices.update(inv)
        notes.extend(nts)
        slack.update(slk)
        if manifest.get("as_of"):
            as_of = date.fromisoformat(manifest["as_of"])
    if as_of is None:
        print("warning: no as_of in any manifest; using today's system date")
        as_of = date.today()
    return invoices, notes, slack, as_of


def triage(invoices, notes, slack, policy, as_of):
    min_dpd = policy["min_days_past_due"]
    stale = policy["stale_touch_days"]
    agent_max = policy["agent_max_amount"]
    writeoff_max = policy["write_off_max_amount"]

    skipped = 0
    sheet_touch, sheet_notes = {}, {}
    for n in notes:
        try:
            d = parse_date(n["touch_date"])
            iid = n["invoice_id"]
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        sheet_notes.setdefault(iid, []).append({"date": n["touch_date"], "collector": n.get("collector"), "note": n.get("note")})
        if d and (iid not in sheet_touch or d > sheet_touch[iid]):
            sheet_touch[iid] = d

    items, conflicts = [], []
    open_count = under_threshold = 0

    for iid, inv in invoices.items():
        status = (inv.get("status") or "").strip().lower()
        if status == "paid":
            continue
        thread = slack.get(iid)
        try:
            balance = round(float(inv["amount"]) - float(inv["amount_paid"] or 0), 2)
            due = date.fromisoformat(inv["due_date"])
            touches = {
                "salesforce": parse_date(inv.get("last_touch_date")),
                "sheet": sheet_touch.get(iid),
                "slack": parse_date(thread.get("last_message_date")) if thread else None,
            }
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        open_count += 1
        if balance <= agent_max:
            under_threshold += 1

        touches = {k: v for k, v in touches.items() if v}
        last_source = max(touches, key=touches.get) if touches else None
        last_touch = touches[last_source] if touches else None

        conflict = None
        if len(touches) >= 2:
            gap = (max(touches.values()) - min(touches.values())).days
            if gap >= stale:
                conflict = {"touches": {k: v.isoformat() for k, v in touches.items()}, "gap_days": gap}
                conflicts.append({"invoice_id": iid, **conflict})

        dpd = (as_of - due).days
        since = (as_of - last_touch).days if last_touch else None
        # A zero or negative balance on an unpaid status is a data problem, not a write-off.
        if dpd < min_dpd or (since is not None and since < stale) or balance > agent_max or balance <= 0:
            continue

        dispute = (inv.get("dispute_flag") or "").strip().lower() in {"true", "1", "yes", "y"} or status == "disputed"
        if balance <= writeoff_max:
            bucket, reason = "writeoff", f"balance ${balance:.2f} is at or under the ${writeoff_max:g} write-off limit"
        elif dispute:
            bucket, reason = "escalate", "disputed"
        else:
            bucket, reason = "agent", "small, aged and untouched"

        items.append({
            "invoice_id": iid,
            "bucket": bucket,
            "reason": reason,
            "account_id": inv["account_id"],
            "account_name": inv["account_name"],
            "account_owner": inv["account_owner"],
            "owner_email": inv["owner_email"],
            "billing_contact_email": inv["billing_contact_email"],
            "currency": inv["currency"],
            "amount": float(inv["amount"]),
            "amount_paid": float(inv["amount_paid"]),
            "balance": balance,
            "status": status,
            "dispute_flag": dispute,
            "issue_date": inv["issue_date"],
            "due_date": inv["due_date"],
            "days_past_due": dpd,
            "last_touch_date": last_touch.isoformat() if last_touch else None,
            "last_touch_source": last_source,
            "days_since_touch": since,
            "never_touched": last_touch is None,
            "conflict": conflict,
            "sheet_notes": sheet_notes.get(iid, []),
            "slack_thread": thread,
        })

    items.sort(key=lambda i: (-i["days_past_due"], -i["balance"], i["invoice_id"]))

    buckets = {b: {"count": 0, "dollars": 0.0} for b in ("agent", "writeoff", "escalate")}
    for item in items:
        buckets[item["bucket"]]["count"] += 1
        buckets[item["bucket"]]["dollars"] += item["balance"]
    for b in buckets.values():
        b["dollars"] = round(b["dollars"], 2)

    totals = {
        "invoices": len(invoices),
        "open": open_count,
        "under_threshold": under_threshold,
        "queued": len(items),
        "queued_dollars": round(sum(b["dollars"] for b in buckets.values()), 2),
        "never_touched": sum(1 for i in items if i["never_touched"]),
        "conflicts_open": len(conflicts),
        "conflicts_queued": sum(1 for i in items if i["conflict"]),
        "malformed_rows_skipped": skipped,
        "by_bucket": buckets,
    }
    return {"as_of": as_of.isoformat(), "policy": policy, "totals": totals, "items": items, "conflicts": conflicts}


def headline(queue):
    t = queue["totals"]
    minutes = queue["policy"]["minutes_per_touch"]
    return {
        "as_of": queue["as_of"],
        "total_invoices": t["invoices"],
        "open": t["open"],
        "under_threshold": t["under_threshold"],
        "queued": t["queued"],
        "queued_dollars": t["queued_dollars"],
        "never_touched": t["never_touched"],
        "conflicts_open": t["conflicts_open"],
        "conflicts_queued": t["conflicts_queued"],
        "malformed_rows_skipped": t["malformed_rows_skipped"],
        "by_bucket": t["by_bucket"],
        "minutes_per_touch": minutes,
        "hours_saved_estimate": round(t["queued"] * minutes / 60, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", type=Path, default=ROOT / "data" / "inbox")
    ap.add_argument("--exports", type=Path, default=ROOT / "data" / "exports")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--policy", type=Path, default=ROOT / "policy.yaml")
    args = ap.parse_args()

    start = time.perf_counter()
    policy = load_policy(args.policy)
    folders = pick_sources(args.inbox, args.exports)
    invoices, notes, slack, as_of = load_sources(folders)
    queue = triage(invoices, notes, slack, policy, as_of)
    queue["sources"] = [str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f) for f in folders]
    head = headline(queue)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "queue.json").write_text(json.dumps(queue, indent=1), encoding="utf-8")
    (args.out / "headline.json").write_text(json.dumps(head, indent=2), encoding="utf-8")
    elapsed = time.perf_counter() - start

    b = queue["totals"]["by_bucket"]
    print(
        f"queued {head['queued']:,} of {head['total_invoices']:,} invoices (${head['queued_dollars']:,.2f}) as of {head['as_of']}: "
        f"agent {b['agent']['count']:,}, writeoff {b['writeoff']['count']:,}, escalate {b['escalate']['count']:,}; "
        f"{head['conflicts_open']:,} source conflicts ({head['conflicts_queued']:,} queued); "
        f"~{head['hours_saved_estimate']:,} collector hours; {elapsed:.2f}s"
    )
    if head["malformed_rows_skipped"]:
        print(f"warning: skipped {head['malformed_rows_skipped']:,} malformed rows")
    print(json.dumps(head, indent=2))


if __name__ == "__main__":
    main()
