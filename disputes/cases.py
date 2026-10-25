"""Payment-blocker cases: the agent keeps a missing-PO dispute open until there is evidence it is resolved.

A case moves: requested (owner asked for the PO) -> accepted (owner took it) -> PO received -> ready (PO checked
against the invoice; a corrected package is drafted) or blocked (the PO contradicts the invoice; the case stays
open with the exact mismatch). A case closes only when the invoice leaves the dispute queue.

Cases live in out/disputes/cases/<invoice_id>.json and survive every run. Owner replies are read from
data/responses/*.json; in the offline demo those files are synthetic and say so.
"""
import json
import math
import os
import re
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


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


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


def load_responses(folder=RESPONSES):
    out = []
    for path in sorted(Path(folder).glob("*.json")):
        data = read_json(path) or {}
        rows = data.get("responses")
        for n, r in enumerate(rows if isinstance(rows, list) else []):
            if isinstance(r, dict) and isinstance(r.get("invoice_id"), str):
                rid = str(r.get("reply_id") or f"{path.name}#{n}")
                out.append({**r, "id": rid, "synthetic": bool(data.get("synthetic")), "_order": (path.name, n)})
    return sorted(out, key=lambda r: (str(r.get("at") or ""), r["_order"]))


def event(case, at, what, detail, source="agent"):
    case["history"].append({"at": at, "event": what, "detail": detail, "source": source})


def verify(po, packet, as_of):
    """Every check must pass on evidence that is present; a missing field is a problem, not a pass."""
    if not isinstance(po, dict):
        return ["the reply has no readable PO"]
    problems = []
    number = po.get("number")
    if not number:
        problems.append("the reply has no PO number")
    if not po.get("account_id"):
        problems.append(f"PO {number} does not say which account it is for")
    elif po["account_id"] != packet["account_id"]:
        problems.append(f"PO {number} is for account {po['account_id']}, but the invoice is on "
                        f"{packet['account_id']} ({packet['account_name']})")
    try:
        amount = float(po.get("amount"))
        if not math.isfinite(amount):
            raise ValueError
        if amount + 0.005 < float(packet["balance"]):
            problems.append(f"PO {number} covers ${amount:,.2f}, less than the ${packet['balance']:,.2f} balance")
    except (TypeError, ValueError):
        problems.append(f"PO {number} has no readable amount")
    try:
        if date.fromisoformat(str(po.get("valid_until"))) < as_of:
            problems.append(f"PO {number} expired on {po['valid_until']}")
    except (TypeError, ValueError):
        problems.append(f"PO {number} has no readable expiry date")
    ids = po.get("invoice_ids")
    if not isinstance(ids, list) or packet["invoice_id"] not in ids:
        problems.append(f"PO {number} does not list {packet['invoice_id']}")
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
        po = r.get("po") if isinstance(r.get("po"), dict) else {}
        case["po"] = po
        event(case, at, "po_received", f"{who} sent PO {po.get('number') or '(no number)'}", tag)
        judge(case, packet, as_of, at)
    else:
        event(case, at, "note", f"{who}: {r.get('message') or ''}".strip(), tag)


def judge(case, packet, as_of, at):
    po = case.get("po") or {}
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
        event(case, at, "verified", f"PO {po['number']} matches the invoice: same account, covers the balance, "
              f"valid until {po['valid_until']}. Corrected package drafted for approval.")


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
        if not SAFE_ID.match(iid):
            continue
        path = folder / f"{iid}.json"
        case = read_json(path) or new_case(packet, as_of)
        case.setdefault("history", [])
        case.setdefault("applied", [])
        if case.get("state") not in LABELS:
            case["state"] = "requested"
        if case["state"] == "closed":
            case.update(state="requested", open_questions=["the PO number for this invoice"])
            case.pop("package", None)
            case.pop("po", None)
            event(case, as_of.isoformat(), "reopened", "Back in the dispute queue; asked the owner again.")
        balance_moved = abs(float(case.get("balance") or 0) - float(packet["balance"])) >= 0.01
        case["balance"] = packet["balance"]
        if balance_moved and case["state"] in ("ready", "blocked") and case.get("po"):
            judge(case, packet, as_of, as_of.isoformat())
        for r in by_invoice.get(iid, []):
            if r["id"] not in case["applied"]:
                apply(case, r, packet, as_of)
                case["applied"].append(r["id"])
        case["label"] = LABELS[case["state"]]
        write_atomic(path, case)
        packet["case"] = {k: case.get(k) for k in ("state", "label", "open_questions", "history", "po", "package")}
        cases.append(case)
    still_disputed = {p["invoice_id"] for p in packets}
    for path in sorted(folder.glob("*.json")):
        if path.stem in live:
            continue
        case = read_json(path)
        if case and case.get("state") != "closed":
            case.setdefault("history", [])
            case["state"] = "closed"
            case["label"] = LABELS["closed"]
            why = ("Still disputed, but no longer for a missing PO; the dispute desk routes it now."
                   if path.stem in still_disputed else "Left the dispute queue: paid, cleared or worked by a person.")
            event(case, as_of.isoformat(), "closed", why)
            write_atomic(path, case)
    return cases


def summarize(cases):
    counts = {s: 0 for s in LABELS}
    for c in cases:
        counts[c["state"]] += 1
    return {"open": sum(v for k, v in counts.items() if k != "closed"), "by_state": counts}
