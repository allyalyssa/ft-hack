"""Write synthetic owner replies for the offline demo: python disputes/make_responses.py

Picks the first three missing-PO disputes from the latest run and writes data/responses/owner_replies.json:
one owner sends a PO that matches (the case becomes ready), one sends a PO for the wrong account (the case stays
blocked), one only accepts the task (the case waits for the PO). Run disputes.py (or the cycle) afterwards.
"""
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISPUTES = ROOT / "out" / "disputes"
OUT = ROOT / "data" / "responses" / "owner_replies.json"


def main():
    packets = []
    for path in sorted(DISPUTES.glob("INV-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("generated_by") == "disputes" and data.get("reason") == "missing_po":
            packets.append(data)
    packets.sort(key=lambda p: ({"P1": 0, "P2": 1, "P3": 2}[p["priority"]], -p["balance"], p["invoice_id"]))
    if len(packets) < 3:
        raise SystemExit("need at least three missing-PO disputes; run triage and disputes first")
    a, b, c = packets[:3]
    as_of = date.fromisoformat(a["as_of"])
    valid = (as_of + timedelta(days=90)).isoformat()
    responses = [
        {"invoice_id": a["invoice_id"], "from": a["owner_email"], "type": "accept", "at": as_of.isoformat(),
         "message": "I'll get the PO from their buyer today."},
        {"invoice_id": a["invoice_id"], "from": a["owner_email"], "type": "po", "at": as_of.isoformat(),
         "po": {"number": "PO-" + a["invoice_id"].split("-")[-1] + "-A", "account_id": a["account_id"],
                "amount": round(a["balance"], 2), "valid_until": valid, "invoice_ids": [a["invoice_id"]]}},
        {"invoice_id": b["invoice_id"], "from": b["owner_email"], "type": "accept", "at": as_of.isoformat(),
         "message": "On it."},
        {"invoice_id": b["invoice_id"], "from": b["owner_email"], "type": "po", "at": as_of.isoformat(),
         "po": {"number": "PO-" + b["invoice_id"].split("-")[-1] + "-B", "account_id": a["account_id"],
                "amount": round(b["balance"], 2), "valid_until": valid, "invoice_ids": [b["invoice_id"]]}},
        {"invoice_id": c["invoice_id"], "from": c["owner_email"], "type": "accept", "at": as_of.isoformat(),
         "message": "Their procurement lead is out until Thursday; I'll chase then."},
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"synthetic": True,
                               "note": "Synthetic owner replies for the offline demo. Not real messages.",
                               "responses": responses}, indent=2), encoding="utf-8")
    print(f"wrote {len(responses)} synthetic owner replies for {a['invoice_id']} (matching PO), "
          f"{b['invoice_id']} (PO for the wrong account), {c['invoice_id']} (accepted, no PO yet) -> {OUT}")


if __name__ == "__main__":
    main()
