"""Deterministic triage: which small invoices has everyone forgotten about?

python triage/triage.py

Reads data/inbox/ (falls back to data/exports/ when the inbox is empty). A bundle whose manifest says
"kind": "delta" is laid over data/exports/ instead of replacing it. Writes out/queue.json and out/headline.json.
"""
import argparse
import csv
import hashlib
import json
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_KEYS = ("min_days_past_due", "stale_touch_days", "agent_max_amount", "write_off_max_amount")
# An open invoice this size means a person owns the relationship; small reminders should go through them.
LARGE_INVOICE = 100_000
# This many write-offs on one account in one run goes to a controller even when each is tiny.
BATCH_SIGNOFF = 5


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
        "account_writeoff_max": float(raw.get("account_writeoff_max", 25)),
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

    items, conflicts, open_rows = [], [], []
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

        dispute = (inv.get("dispute_flag") or "").strip().lower() in {"true", "1", "yes", "y"} or status == "disputed"
        open_rows.append({"invoice_id": iid, "account_id": inv.get("account_id"), "balance": balance,
                          "last_touch": last_touch, "last_touch_source": last_source, "dispute": dispute})

        dpd = (as_of - due).days
        since = (as_of - last_touch).days if last_touch else None
        # A zero or negative balance on an unpaid status is a data problem, not a write-off.
        if dpd < min_dpd or (since is not None and since < stale) or balance > agent_max or balance <= 0:
            continue

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
    policy_version = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:12]
    packages = add_account_context(items, open_rows, policy, as_of)
    leads = add_writeoff_controls(items, policy, policy_version)

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
        "packages": len(packages),
        "invoices_in_shared_packages": sum(p["count"] for p in packages if p["count"] > 1),
        "agent_on_large_accounts": sum(1 for i in items if i["bucket"] == "agent" and i["account_context"]["has_large_open"]),
        "coordinate_with_owner": sum(1 for i in items if i["coordinate"]),
        "writeoff_residuals": sum(1 for i in items if i.get("tiny_origin") == "residual"),
        "writeoff_issued_tiny": sum(1 for i in items if i.get("tiny_origin") == "issued_tiny"),
        "writeoff_needs_controller": sum(1 for i in items if (i.get("writeoff_controls") or {}).get("needs_controller")),
        "billing_leads": len(leads),
    }
    return {"as_of": as_of.isoformat(), "policy": policy, "policy_version": policy_version, "totals": totals,
            "items": items, "packages": packages, "billing_leads": leads, "conflicts": conflicts}


def add_account_context(items, open_rows, policy, as_of):
    """Look at the whole account before anyone chases a small invoice on it, and group queued invoices by account."""
    stale = policy["stale_touch_days"]
    open_by_account, queued_by_account = {}, {}
    for r in open_rows:
        open_by_account.setdefault(r["account_id"], []).append(r)
    for item in items:
        queued_by_account.setdefault(item["account_id"], []).append(item)

    def brief(r):
        return {"invoice_id": r["invoice_id"], "balance": r["balance"],
                "last_touch_date": r["last_touch"].isoformat() if r["last_touch"] else None,
                "last_touch_source": r["last_touch_source"]}

    for item in items:
        rows = open_by_account.get(item["account_id"], [])
        others = [r for r in rows if r["invoice_id"] != item["invoice_id"]]
        largest = max(others, key=lambda r: r["balance"], default=None)
        latest = max((r for r in others if r["last_touch"]), key=lambda r: r["last_touch"], default=None)
        item["package_id"] = f"PKG-{item['account_id']}"
        item["account_context"] = {
            "open_invoices": len(rows),
            "open_balance": round(sum(r["balance"] for r in rows), 2),
            "queued_on_account": len(queued_by_account[item["account_id"]]),
            "largest_other": brief(largest) if largest else None,
            "has_large_open": any(r["balance"] > LARGE_INVOICE for r in others),
            "open_disputes": sum(1 for r in others if r["dispute"]),
            "last_account_touch": brief(latest) if latest else None,
        }
        item["coordinate"] = None
        live = [r for r in others if r["balance"] > LARGE_INVOICE and r["last_touch"]
                and (as_of - r["last_touch"]).days < stale]
        if live:
            big = max(live, key=lambda r: r["last_touch"])
            days = (as_of - big["last_touch"]).days
            when = "today" if days == 0 else f"{days} day{'s' if days > 1 else ''} ago"
            item["coordinate"] = {
                "invoice_id": big["invoice_id"],
                "balance": big["balance"],
                "touched": big["last_touch"].isoformat(),
                "source": big["last_touch_source"],
                "owner": item["account_owner"],
                "reason": f"Someone is working {big['invoice_id']} (${big['balance']:,.2f}) on this account: last "
                          f"touched {when} via {big['last_touch_source']}. Fold this invoice into that conversation "
                          f"instead of sending a separate reminder.",
            }

    packages = []
    for account_id, its in queued_by_account.items():
        first = its[0]
        buckets = {}
        for i in its:
            buckets[i["bucket"]] = buckets.get(i["bucket"], 0) + 1
        packages.append({
            "package_id": f"PKG-{account_id}",
            "account_id": account_id,
            "account_name": first["account_name"],
            "account_owner": first["account_owner"],
            "owner_email": first["owner_email"],
            "invoice_ids": [i["invoice_id"] for i in its],
            "count": len(its),
            "dollars": round(sum(i["balance"] for i in its), 2),
            "by_bucket": buckets,
            "oldest_days_past_due": max(i["days_past_due"] for i in its),
            "coordinate": any(i["coordinate"] for i in its),
            "has_large_open": first["account_context"]["has_large_open"],
            "open_balance": first["account_context"]["open_balance"],
        })
    packages.sort(key=lambda p: (-p["dollars"], p["account_id"]))
    return packages


def add_writeoff_controls(items, policy, policy_version):
    """A $3 balance is not enough on its own: check what the whole account would write off at once."""
    limit = policy.get("account_writeoff_max", 25.0)
    per_account = {}
    for i in items:
        if i["bucket"] == "writeoff":
            per_account.setdefault(i["account_id"], []).append(i)
    for its in per_account.values():
        total = round(sum(i["balance"] for i in its), 2)
        for i in its:
            reasons = []
            if total > limit:
                reasons.append(f"the account would write off ${total:,.2f} across {len(its)} invoices, "
                               f"over the ${limit:g} account limit")
            if len(its) >= BATCH_SIGNOFF:
                reasons.append(f"{len(its)} write-offs on one account at once; a controller signs off a batch this size")
            i["tiny_origin"] = "residual" if i["amount_paid"] > 0 else "issued_tiny"
            i["writeoff_controls"] = {
                "account_writeoff_total": total,
                "account_writeoff_count": len(its),
                "account_limit": limit,
                "needs_controller": bool(reasons),
                "reasons": reasons or ["within the per-invoice and account limits"],
                "policy_version": policy_version,
            }
    leads = []
    for account_id, its in per_account.items():
        if len(its) >= 3:
            residuals = sum(1 for i in its if i["tiny_origin"] == "residual")
            leads.append({
                "account_id": account_id,
                "account_name": its[0]["account_name"],
                "count": len(its),
                "dollars": round(sum(i["balance"] for i in its), 2),
                "residuals": residuals,
                "issued_tiny": len(its) - residuals,
                "invoice_ids": [i["invoice_id"] for i in its],
                "note": "A pattern for billing operations to look at, not a proven billing error: "
                        + ("mostly leftovers after partial payments." if residuals * 2 >= len(its)
                           else "mostly invoices issued for a few dollars or cents."),
            })
    leads.sort(key=lambda lead: (-lead["count"], -lead["dollars"]))
    return leads


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
        "packages": t["packages"],
        "invoices_in_shared_packages": t["invoices_in_shared_packages"],
        "agent_on_large_accounts": t["agent_on_large_accounts"],
        "coordinate_with_owner": t["coordinate_with_owner"],
        "writeoff_residuals": t["writeoff_residuals"],
        "writeoff_issued_tiny": t["writeoff_issued_tiny"],
        "writeoff_needs_controller": t["writeoff_needs_controller"],
        "billing_leads": t["billing_leads"],
        "policy_version": queue["policy_version"],
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
