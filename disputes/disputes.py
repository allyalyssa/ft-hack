"""Work the escalate bucket: every disputed small invoice gets a reason, an owner, a deadline and drafts.

python disputes/disputes.py        # after triage; reads out/queue.json, writes out/disputes/

No model. Reasons come from the collector-sheet notes and Slack threads that triage attached to each item.
Per invoice: out/disputes/<invoice_id>.json with the reason and its evidence, who it goes to, priority, a
respond-by date, a recommended resolution, a Salesforce dispute case, a note to the account owner and a
holding reply to the customer that does not ask for payment. Also out/disputes/salesforce_cases.csv (one row per
case, ready for Data Loader) and out/disputes/summary.json for the deck.
"""
import argparse
import csv
import io
import json
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEAMS = {
    "deal_desk": "dealdesk@ourco.example",
    "billing_ops": "billing-ops@ourco.example",
    "account_owner": None,
}

# The newest note or message that matches decides the reason; within one line, earlier reasons win.
REASONS = [
    ("already_paid", re.compile(r"\b(?:was|already|been) paid\b|\bpaid but\b")),
    ("usage_overage", re.compile(r"\boverages?\b|\busage\b|\bmetering\b")),
    ("missing_po", re.compile(r"\bpo number\b|\bpurchase order\b|\bvendor portal\b")),
    ("invoice_admin", re.compile(r"\bconsolidat\w*|\bcopy of the invoice\b|\bresen[dt]\b")),
    ("payment_promised", re.compile(r"\bpayment run\b|\bpayment scheduled\b|\bpromised to pay\b|\bshould clear\b")),
]
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

PLAYBOOK = {
    "already_paid": {
        "label": "Customer says it was paid",
        "route": "billing_ops",
        "resolution": "Search unapplied cash and recent deposits for this customer. Apply the payment if found; "
                      "otherwise ask the customer for the remittance details.",
    },
    "usage_overage": {
        "label": "Disputes a usage overage",
        "route": "deal_desk",
        "resolution": "Pull the usage report for the billing period and compare it with the contract. Credit the "
                      "difference if metering or the rate was wrong; otherwise send the usage detail.",
    },
    "missing_po": {
        "label": "Needs a PO or vendor-portal upload",
        "route": "account_owner",
        "resolution": "Get the PO number from the buyer, reissue the invoice with it and upload it to the "
                      "customer's vendor portal.",
    },
    "invoice_admin": {
        "label": "Invoice paperwork (copy or consolidation)",
        "route": "billing_ops",
        "resolution": "Resend or consolidate the invoice as asked, then clear the dispute flag.",
    },
    "payment_promised": {
        "label": "Customer has since promised payment",
        "route": "account_owner",
        "resolution": "The dispute flag looks stale: the latest notes say payment is coming. Confirm with the "
                      "customer, clear the flag and return the invoice to the normal agent queue.",
    },
    "no_reason": {
        "label": "No reason recorded",
        "route": "account_owner",
        "resolution": "Nobody wrote down why this is disputed. Ask the customer for the reason, then route it.",
    },
}

SLA_DAYS = {"P1": 2, "P2": 5, "P3": 10}


def add_business_days(d, n):
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def evidence_lines(item):
    lines = [(n.get("date") or "", f"collector sheet, {n.get('collector') or 'unknown'}", n.get("note") or "")
             for n in item.get("sheet_notes") or []]
    thread = item.get("slack_thread") or {}
    lines += [(m.get("date") or "", f"Slack {thread.get('channel') or ''}, {m.get('user') or 'unknown'}", m.get("text") or "")
              for m in thread.get("messages") or []]
    return sorted(lines, reverse=True)


def classify(item):
    lines = evidence_lines(item)
    for line in lines:
        text = line[2].lower()
        for reason, pattern in REASONS:
            if pattern.search(text):
                return reason, [l for l in lines if pattern.search(l[2].lower())]
    return "no_reason", lines[:2]


def priority(item):
    if item["balance"] >= 250 or item["days_past_due"] >= 90:
        return "P1"
    if item["days_past_due"] >= 60:
        return "P2"
    return "P3"


def case_id(item):
    return "DSP-" + item["invoice_id"]


def long_date(d):
    return f"{d:%B} {d.day}"


def packet(item, as_of, account_queue, concede_max):
    reason, hits = classify(item)
    play = PLAYBOOK[reason]
    prio = priority(item)
    respond_by = add_business_days(as_of, SLA_DAYS[prio])
    concede = item["balance"] <= concede_max and reason not in ("already_paid", "payment_promised")
    if concede:
        resolution = (f"Balance ${item['balance']:,.2f} is at or under the ${concede_max:g} concede limit: approve a "
                      f"credit memo and close the case. Arguing it costs more than the invoice.")
    else:
        resolution = play["resolution"]
    team = play["route"]
    owner_email = item.get("owner_email")
    if not owner_email and team == "account_owner":
        team = "billing_ops"
    route_email = TEAMS[team] or owner_email
    others = [i for i in account_queue.get(item["account_id"], []) if i["invoice_id"] != item["invoice_id"]]
    bal = f"${item['balance']:,.2f}"
    owner_name = (item.get("account_owner") or "").split()
    first = owner_name[0] if owner_name and route_email == owner_email else "team"
    evidence_text = "\n".join(f"- {d} ({src}): {text}" for d, src, text in hits) or "- nothing recorded"
    others_text = (f"\n{item['account_name']} also has {len(others)} other "
                   f"invoice{'s' if len(others) > 1 else ''} in the queue, "
                   f"${sum(i['balance'] for i in others):,.2f} in total." if others else "")
    cid = case_id(item)

    return {
        "invoice_id": item["invoice_id"],
        "case_id": cid,
        "as_of": as_of.isoformat(),
        "account_id": item["account_id"],
        "account_name": item["account_name"],
        "generated_by": "disputes",
        "account_owner": item.get("account_owner"),
        "owner_email": owner_email,
        "balance": item["balance"],
        "days_past_due": item["days_past_due"],
        "status": item["status"],
        "reason": reason,
        "reason_label": play["label"],
        "evidence": [{"date": d, "source": s, "text": t} for d, s, t in hits],
        "route_to": team,
        "route_email": route_email,
        "priority": prio,
        "respond_by": respond_by.isoformat(),
        "concede_recommended": concede,
        "resolution": resolution,
        "account_other_queued": {"count": len(others), "dollars": round(sum(i["balance"] for i in others), 2)},
        "salesforce_case": {
            "case_id": cid,
            "type": "Billing Dispute",
            "status": "New",
            "origin": "Collections agent",
            "priority": {"P1": "High", "P2": "Medium", "P3": "Low"}[prio],
            "subject": f"Dispute: {item['invoice_id']} {item['account_name']} {bal} ({play['label'].lower()})"[:255],
            "description": f"{item['days_past_due']} days past due. Reason: {play['label']}.\n"
                           f"Recommended: {resolution}\nEvidence:\n{evidence_text}",
        },
        "owner_note": {
            "to": route_email,
            "cc": owner_email if owner_email and route_email != owner_email else None,
            "subject": f"[{prio}] Disputed invoice {item['invoice_id']}, {item['account_name']}, {bal}: respond by {respond_by:%b} {respond_by.day}",
            "body": f"Hi {first},\n\n"
                    f"{item['invoice_id']} for {item['account_name']} ({bal}, {item['days_past_due']} days past due) is "
                    f"disputed. Reason: {play['label']}.\n\n{evidence_text}\n\n"
                    f"Recommended next step: {resolution}{others_text}\n\n"
                    f"Case {cid} is open. Please reply by {respond_by:%A}, {long_date(respond_by)}.\n\nOurCo Collections",
        },
        "customer_ack": {
            "to": item.get("billing_contact_email"),
            "subject": f"Your question about invoice {item['invoice_id']}",
            "body": "Hello,\n\nThank you for raising your question about invoice "
                    f"{item['invoice_id']}. We have opened case {cid} and our team is reviewing it now. "
                    + ("Could you reply with a short note on what looks wrong, so we can sort it quickly? "
                       if reason == "no_reason" and not concede else "")
                    + f"We aim to update you by {long_date(respond_by)}, and we won't send payment reminders for this "
                    "invoice while we review it."
                    "\n\nOurCo Collections",
        },
    }


def run(queue, concede_max):
    as_of = date.fromisoformat(queue["as_of"])
    account_queue = {}
    for i in queue["items"]:
        account_queue.setdefault(i["account_id"], []).append(i)
    order = {"P1": 0, "P2": 1, "P3": 2}
    packets = [packet(i, as_of, account_queue, concede_max) for i in queue["items"] if i["bucket"] == "escalate"]
    packets.sort(key=lambda p: (order[p["priority"]], -p["balance"], p["invoice_id"]))
    return packets


def summarize(packets, queue):
    def roll(key):
        out = {}
        for p in packets:
            s = out.setdefault(p[key], {"count": 0, "dollars": 0.0})
            s["count"] += 1
            s["dollars"] = round(s["dollars"] + p["balance"], 2)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))

    concede = [p for p in packets if p["concede_recommended"]]
    return {
        "as_of": queue["as_of"],
        "disputes": len(packets),
        "dollars": round(sum(p["balance"] for p in packets), 2),
        "by_reason": roll("reason"),
        "by_route": roll("route_to"),
        "by_priority": roll("priority"),
        "no_reason_recorded": sum(1 for p in packets if p["reason"] == "no_reason"),
        "stale_flags": sum(1 for p in packets if p["reason"] == "payment_promised"),
        "concede_recommended": {"count": len(concede), "dollars": round(sum(p["balance"] for p in concede), 2)},
        "oldest_days_past_due": max((p["days_past_due"] for p in packets), default=0),
    }


CSV_FIELDS = ["Case_External_Id__c", "Invoice__c", "Account_External_Id__c", "Type", "Status", "Origin", "Priority",
              "Subject", "Description", "Route_To__c", "Respond_By__c", "Amount__c"]


def cell(value):
    # Evidence text comes from customers; stop a spreadsheet from treating it as a formula.
    s = str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def write_atomic(path, text):
    # The dispute desk polls these files; never let it read half a file.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def write(packets, summary, out):
    out.mkdir(parents=True, exist_ok=True)
    packets = [p for p in packets if SAFE_ID.match(p["invoice_id"])]
    keep = {f"{p['invoice_id']}.json" for p in packets}
    for old in out.glob("*.json"):
        if old.name in keep or old.name == "summary.json":
            continue
        try:
            if json.loads(old.read_text(encoding="utf-8")).get("generated_by") == "disputes":
                old.unlink()
        except (ValueError, AttributeError, OSError):
            pass
    for p in packets:
        write_atomic(out / f"{p['invoice_id']}.json", json.dumps(p, indent=2))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    for p in packets:
        c = p["salesforce_case"]
        w.writerow([cell(v) for v in (c["case_id"], p["invoice_id"], p["account_id"], c["type"], c["status"],
                    c["origin"], c["priority"], c["subject"], c["description"], p["route_email"], p["respond_by"],
                    f"{p['balance']:.2f}")])
    write_atomic(out / "salesforce_cases.csv", buf.getvalue())
    write_atomic(out / "summary.json", json.dumps(summary, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, default=ROOT / "out" / "queue.json")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "disputes")
    ap.add_argument("--concede-max", type=float, default=25.0,
                    help="recommend crediting disputes at or under this balance instead of arguing them")
    args = ap.parse_args()

    start = time.perf_counter()
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    packets = run(queue, args.concede_max)
    summary = summarize(packets, queue)
    write(packets, summary, args.out)
    r = summary["by_reason"]
    print(f"disputes: {summary['disputes']:,} (${summary['dollars']:,.2f}) as of {summary['as_of']}; "
          f"{summary['by_priority'].get('P1', {}).get('count', 0)} P1; "
          + ", ".join(f"{k} {v['count']}" for k, v in r.items())
          + f"; concede {summary['concede_recommended']['count']} (${summary['concede_recommended']['dollars']:,.2f}); "
          f"{time.perf_counter() - start:.2f}s")


if __name__ == "__main__":
    main()
