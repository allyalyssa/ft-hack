"""Payment-blocker cases: the agent keeps a missing-PO dispute open until there is evidence it is resolved.

A case moves: requested (owner asked for the PO) -> accepted (owner took it) -> PO received -> ready (PO checked
against the invoice; a corrected package is drafted) or blocked (the PO contradicts the invoice; the case stays
open with the exact mismatch). A case closes only when the invoice leaves the dispute queue.

Cases live in out/disputes/cases/<invoice_id>.json and survive every run. Owner replies are read from
data/responses/*.json; in the offline demo those files are synthetic and say so.
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "out" / "disputes" / "cases"
RESPONSES = ROOT / "data" / "responses"
WORKFLOW_REASONS = ("missing_po",)
LABELS = {
    "requested": "Waiting on the owner",
    "accepted": "Owner is getting the PO",
    "ready": "PO checked, package ready",
    "blocked": "PO doesn't match",
    "closed": "Closed",
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_responses(folder=RESPONSES):
    out = []
    for path in sorted(Path(folder).glob("*.json")):
        data = read_json(path) or {}
        for n, r in enumerate(data.get("responses") or []):
            if isinstance(r, dict) and r.get("invoice_id"):
                out.append({**r, "id": f"{path.name}#{n}", "synthetic": bool(data.get("synthetic"))})
    return sorted(out, key=lambda r: (str(r.get("at") or ""), r["id"]))


def event(case, at, what, detail, source="agent"):
    case["history"].append({"at": at, "event": what, "detail": detail, "source": source})


def verify(po, packet, as_of):
    problems = []
    if po.get("account_id") and po["account_id"] != packet["account_id"]:
        problems.append(f"PO {po.get('number')} is for account {po['account_id']}, but the invoice is on "
                        f"{packet['account_id']} ({packet['account_name']})")
    try:
        if float(po.get("amount") or 0) + 0.005 < float(packet["balance"]):
            problems.append(f"PO {po.get('number')} covers ${float(po.get('amount') or 0):,.2f}, less than the "
                            f"${packet['balance']:,.2f} balance")
    except (TypeError, ValueError):
        problems.append("the PO amount is unreadable")
    if po.get("valid_until"):
        try:
            if date.fromisoformat(po["valid_until"]) < as_of:
                problems.append(f"PO {po.get('number')} expired on {po['valid_until']}")
        except ValueError:
            problems.append("the PO expiry date is unreadable")
    if po.get("invoice_ids") and packet["invoice_id"] not in po["invoice_ids"]:
        problems.append(f"PO {po.get('number')} does not list {packet['invoice_id']}")
    if not po.get("number"):
        problems.append("the reply has no PO number")
    return problems


def package(po, packet):
    bal = f"${packet['balance']:,.2f}"
    return {
        "customer_email": {
            "to": packet["customer_ack"]["to"],
            "subject": f"Invoice {packet['invoice_id']} reissued with PO {po['number']}",
            "body": f"Hello,\n\nHere is invoice {packet['invoice_id']} for {bal}, reissued with your purchase order "
                    f"number {po['number']}. If it needs to go through your vendor portal, reply and we will upload it."
                    f"\n\nOurCo Collections",
        },
        "salesforce_update": {
            "case_id": packet["case_id"],
            "status": "Pending customer payment",
            "note": f"PO {po['number']} received from the account owner and checked against {packet['invoice_id']} "
                    f"(account {packet['account_id']}, covers {bal}). Invoice reissued with the PO; reminder paused "
                    f"until the customer's next payment run.",
        },
        "steps": ["Approve the reissued invoice email", "Attach the invoice PDF with the PO number",
                  "Upload to the customer's vendor portal if they use one"],
    }


def apply(case, r, packet, as_of):
    who = r.get("from") or "the account owner"
    tag = "synthetic owner reply" if r["synthetic"] else "owner reply"
    kind = r.get("type")
    at = str(r.get("at") or as_of.isoformat())
    if kind == "accept":
        if case["state"] == "requested":
            case["state"] = "accepted"
        event(case, at, "accepted", f"{who} took it: \"{r.get('message') or 'on it'}\"", tag)
    elif kind == "po":
        po = r.get("po") or {}
        case["po"] = po
        event(case, at, "po_received", f"{who} sent PO {po.get('number') or '(no number)'}", tag)
        problems = verify(po, packet, as_of)
        if problems:
            case["state"] = "blocked"
            case["open_questions"] = problems
            case.pop("package", None)
            event(case, at, "blocked", "Kept open: " + "; ".join(problems) + ". Asked the owner for the right PO.")
        else:
            case["state"] = "ready"
            case["open_questions"] = []
            case["package"] = package(po, packet)
            event(case, at, "verified", f"PO {po['number']} matches the invoice: same account, covers the balance"
                  + (f", valid until {po['valid_until']}" if po.get("valid_until") else "")
                  + ". Corrected package drafted for approval.")
    else:
        event(case, at, "note", f"{who}: {r.get('message') or ''}".strip(), tag)


def new_case(packet, as_of):
    case = {
        "invoice_id": packet["invoice_id"],
        "case_id": packet["case_id"],
        "workflow": packet["reason"],
        "account_id": packet["account_id"],
        "account_name": packet["account_name"],
        "balance": packet["balance"],
        "owner_email": packet.get("owner_email"),
        "opened": as_of.isoformat(),
        "state": "requested",
        "open_questions": ["the PO number for this invoice"],
        "history": [],
        "applied": [],
    }
    event(case, as_of.isoformat(), "requested",
          f"Asked {packet.get('owner_email') or 'the account owner'} for the PO number; the customer can't pay "
          f"{packet['invoice_id']} without it.")
    return case


def advance(packets, responses, as_of, folder=CASES):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    by_invoice = {}
    for r in responses:
        by_invoice.setdefault(r["invoice_id"], []).append(r)
    live = {p["invoice_id"]: p for p in packets if p["reason"] in WORKFLOW_REASONS}
    cases = []
    for iid, packet in live.items():
        path = folder / f"{iid}.json"
        case = read_json(path) or new_case(packet, as_of)
        if case["state"] == "closed":
            case["state"] = "requested"
            event(case, as_of.isoformat(), "reopened", "Back in the dispute queue; asked the owner again.")
        case["balance"] = packet["balance"]
        for r in by_invoice.get(iid, []):
            if r["id"] not in case["applied"]:
                apply(case, r, packet, as_of)
                case["applied"].append(r["id"])
        case["label"] = LABELS[case["state"]]
        path.write_text(json.dumps(case, indent=2), encoding="utf-8")
        packet["case"] = {k: case.get(k) for k in ("state", "label", "open_questions", "history", "po", "package")}
        cases.append(case)
    for path in sorted(folder.glob("INV-*.json")):
        if path.stem in live:
            continue
        case = read_json(path)
        if case and case.get("state") != "closed":
            case["state"] = "closed"
            case["label"] = LABELS["closed"]
            event(case, as_of.isoformat(), "closed", "Left the dispute queue: paid, cleared or worked by a person.")
            path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    return cases


def summarize(cases):
    counts = {s: 0 for s in LABELS}
    for c in cases:
        counts[c["state"]] += 1
    return {"open": sum(v for k, v in counts.items() if k != "closed"), "by_state": counts}
