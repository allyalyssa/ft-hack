"""python -m pytest disputes/   or   python disputes/test_cases.py"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cases  # noqa: E402

AS_OF = date(2026, 10, 25)


def packet(iid="INV-1", reason="missing_po", balance=120.0, account="ACC-1"):
    return {"invoice_id": iid, "case_id": f"DSP-{iid}", "reason": reason, "account_id": account,
            "account_name": "Test Labs", "balance": balance, "owner_email": "owner@ourco.example",
            "customer_ack": {"to": "ap@testlabs.example"}}


def po(**over):
    p = {"number": "PO-77", "account_id": "ACC-1", "amount": 120.0, "valid_until": "2027-01-01", "invoice_ids": ["INV-1"]}
    p.update(over)
    return p


def reply(kind, iid="INV-1", n=0, **extra):
    return {"invoice_id": iid, "type": kind, "from": "owner@ourco.example", "at": "2026-10-25", "id": f"r.json#{n}",
            "synthetic": True, **extra}


def run(packets, responses, folder):
    return {c["invoice_id"]: c for c in cases.advance(packets, responses, AS_OF, folder)}


def test_missing_po_dispute_opens_a_case_waiting_on_the_owner():
    with tempfile.TemporaryDirectory() as tmp:
        got = run([packet(), packet("INV-2", reason="usage_overage")], [], tmp)
    assert list(got) == ["INV-1"]
    assert got["INV-1"]["state"] == "requested"


def test_matching_po_makes_the_case_ready_with_a_package():
    with tempfile.TemporaryDirectory() as tmp:
        p = packet()
        got = run([p], [reply("accept"), reply("po", n=1, po=po())], tmp)
    case = got["INV-1"]
    assert case["state"] == "ready"
    assert "PO-77" in case["package"]["customer_email"]["subject"]
    assert p["case"]["state"] == "ready"
    assert [h["event"] for h in case["history"]] == ["requested", "accepted", "po_received", "verified"]


def test_contradictory_po_keeps_the_case_open_with_each_problem():
    with tempfile.TemporaryDirectory() as tmp:
        got = run([packet()], [reply("po", po=po(account_id="ACC-9", amount=50.0, valid_until="2026-01-01",
                                                  invoice_ids=["INV-8"]))], tmp)
    case = got["INV-1"]
    assert case["state"] == "blocked"
    text = " ".join(case["open_questions"])
    for bit in ("ACC-9", "$50.00", "expired", "does not list INV-1"):
        assert bit in text
    assert "package" not in case


def test_replies_apply_once_and_cases_survive_runs():
    with tempfile.TemporaryDirectory() as tmp:
        responses = [reply("accept")]
        run([packet()], responses, tmp)
        again = run([packet()], responses, tmp)
    assert len(again["INV-1"]["history"]) == 2
    assert again["INV-1"]["state"] == "accepted"


def test_case_closes_when_the_invoice_leaves_the_queue_and_reopens_if_it_returns():
    with tempfile.TemporaryDirectory() as tmp:
        run([packet()], [], tmp)
        cases.advance([], [], AS_OF, tmp)
        closed = json.loads((Path(tmp) / "INV-1.json").read_text())
        back = run([packet()], [], tmp)
    assert closed["state"] == "closed"
    assert back["INV-1"]["state"] == "requested"
    assert back["INV-1"]["history"][-1]["event"] == "reopened"


def test_responses_are_read_from_files_and_marked_synthetic():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "owner.json").write_text(json.dumps({"synthetic": True, "responses": [
            {"invoice_id": "INV-1", "type": "accept", "at": "2026-10-25"}, {"bad": "row"}]}))
        got = cases.load_responses(tmp)
    assert len(got) == 1 and got[0]["synthetic"] is True and got[0]["id"] == "owner.json#0"


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
