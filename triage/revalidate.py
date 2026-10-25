"""Re-check every pending or approved draft against the newest export before anything is sent.

python triage/revalidate.py        # after triage; reads out/actions/<invoice_id>/ and the current export

A draft is withdrawn when the invoice was paid, its balance changed, the customer disputed it, the billing contact
changed, a person touched it after the draft was made, or it left the agent's queue. Writes
out/actions/<invoice_id>/revalidation.json beside each draft and out/revalidation.json with the totals.
It never edits the draft or its status.json; the dashboard and the sender read revalidation.json and hold
anything marked "withdraw".
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CHECKED_STATES = ("drafted", "edited", "approved")


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def email(value):
    return (value or "").strip().lower()


def latest_touch(iid, inv, notes_by_id, slack):
    touches = []
    if inv.get("last_touch_date"):
        touches.append((inv["last_touch_date"], "salesforce"))
    for n in notes_by_id.get(iid, []):
        if n.get("touch_date"):
            touches.append((n["touch_date"], f"collector sheet ({n.get('collector') or 'unknown'})"))
    thread = slack.get(iid)
    if thread and thread.get("last_message_date"):
        touches.append((thread["last_message_date"], f"Slack {thread.get('channel') or ''}".strip()))
    return max(touches) if touches else (None, None)


def check(drafted, inv, queued, notes_by_id, slack):
    """Return the reasons a draft made from `drafted` (the triage row at draft time) no longer holds."""
    iid = drafted["invoice_id"]
    if inv is None:
        return ["the invoice is no longer in the export"]
    reasons = []
    status = (inv.get("status") or "").strip().lower()
    try:
        balance = round(float(inv["amount"]) - float(inv.get("amount_paid") or 0), 2)
    except (KeyError, TypeError, ValueError):
        return ["the invoice row in the new export is unreadable"]
    if status == "paid" or balance <= 0:
        reasons.append("paid since the draft was made")
    elif abs(balance - float(drafted["balance"])) >= 0.01:
        reasons.append(f"balance changed from ${float(drafted['balance']):,.2f} to ${balance:,.2f}")
    disputed = (inv.get("dispute_flag") or "").strip().lower() in {"true", "1", "yes", "y"} or status == "disputed"
    if disputed and not drafted.get("dispute_flag"):
        reasons.append("the customer has disputed it")
    old_contact, new_contact = drafted.get("billing_contact_email"), inv.get("billing_contact_email")
    if email(old_contact) and email(new_contact) and email(old_contact) != email(new_contact):
        reasons.append(f"billing contact changed from {old_contact} to {new_contact}")
    when, where = latest_touch(iid, inv, notes_by_id, slack)
    if when and (not drafted.get("last_touch_date") or when > drafted["last_touch_date"]):
        reasons.append(f"a person touched it on {when} via {where}")
    if not reasons and queued is not None and iid not in queued:
        reasons.append("it is no longer in the agent's queue")
    return reasons


def revalidate(actions_dir, invoices, notes, slack, queue, now=None):
    notes_by_id = {}
    for n in notes:
        if n.get("invoice_id"):
            notes_by_id.setdefault(n["invoice_id"], []).append(n)
    # Without a readable queue we cannot tell "left the queue" from "queue missing", so skip that one check.
    queued = {i["invoice_id"] for i in queue["items"]} if isinstance(queue.get("items"), list) else None
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    results, skipped = [], 0
    for folder in sorted(p for p in Path(actions_dir).glob("*") if p.is_dir()):
        side = folder / "revalidation.json"
        status = read_json(folder / "status.json")
        drafted = read_json(folder / "queue_item.json")
        if not status or not drafted or status.get("state") not in CHECKED_STATES:
            side.unlink(missing_ok=True)  # never leave an old verdict on a redrafted or finished action
            continue
        try:
            reasons = check(drafted, invoices.get(drafted["invoice_id"]), queued, notes_by_id, slack)
        except (KeyError, TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        result = {
            "invoice_id": drafted["invoice_id"],
            "action_id": status.get("action_id"),
            "state": status.get("state"),
            "state_at": status.get("at"),
            "verdict": "withdraw" if reasons else "still_valid",
            "reasons": reasons,
            "drafted_from_snapshot": (status.get("action_id") or "").rsplit(":", 1)[-1] or None,
            "checked_against": queue.get("as_of"),
            "checked_at": stamp,
        }
        write_atomic(side, result)
        results.append(result)
    withdrawn = [r for r in results if r["verdict"] == "withdraw"]
    return {
        "checked_at": stamp,
        "as_of": queue.get("as_of"),
        "checked": len(results),
        "withdrawn": len(withdrawn),
        "withdrawn_approved": sum(1 for r in withdrawn if r["state"] in ("approved", "edited")),
        "still_valid": len(results) - len(withdrawn),
        "unreadable": skipped,
        "queue_checked": queued is not None,
        "items": withdrawn,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", type=Path, default=ROOT / "data" / "inbox")
    ap.add_argument("--exports", type=Path, default=ROOT / "data" / "exports")
    ap.add_argument("--actions", type=Path, default=ROOT / "out" / "actions")
    ap.add_argument("--queue", type=Path, default=ROOT / "out" / "queue.json")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "revalidation.json")
    args = ap.parse_args()

    invoices, notes, slack, as_of = triage.load_sources(triage.pick_sources(args.inbox, args.exports))
    queue = read_json(args.queue) or {"as_of": as_of.isoformat()}
    if not isinstance(queue.get("items"), list):
        print(f"warning: {args.queue} unreadable; skipping the 'left the queue' check this run")
    report = revalidate(args.actions, invoices, notes, slack, queue)
    write_atomic(args.out, report)
    print(f"revalidated {report['checked']} drafts against {report['as_of']}: {report['withdrawn']} withdrawn "
          f"({report['withdrawn_approved']} already approved), {report['still_valid']} still valid")
    for r in report["items"][:10]:
        print(f"  {r['invoice_id']} ({r['state']}): {'; '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
