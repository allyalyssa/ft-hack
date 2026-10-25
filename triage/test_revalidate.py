"""python -m pytest triage/   or   python triage/test_revalidate.py"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import revalidate  # noqa: E402

DRAFTED = {"invoice_id": "INV-1", "balance": 120.0, "dispute_flag": False, "billing_contact_email": "ap@test.example",
           "last_touch_date": "2026-10-01"}


def row(**over):
    r = {"invoice_id": "INV-1", "status": "open", "amount": "120.00", "amount_paid": "0.00", "dispute_flag": "false",
         "billing_contact_email": "ap@test.example", "last_touch_date": "2026-10-01"}
    r.update(over)
    return r


def reasons(inv, notes=(), slack=None, queued=("INV-1",)):
    by_id = {}
    for n in notes:
        by_id.setdefault(n["invoice_id"], []).append(n)
    return revalidate.check(DRAFTED, inv, set(queued), by_id, slack or {})


def test_unchanged_invoice_stays_valid():
    assert reasons(row()) == []


def test_paid_invoice_is_withdrawn():
    assert reasons(row(status="paid", amount_paid="120.00")) == ["paid since the draft was made"]


def test_partial_payment_changes_the_balance():
    assert reasons(row(status="partially_paid", amount_paid="60.00")) == ["balance changed from $120.00 to $60.00"]


def test_new_dispute_withdraws():
    assert reasons(row(status="disputed")) == ["the customer has disputed it"]


def test_new_billing_contact_withdraws():
    assert reasons(row(billing_contact_email="ar@test.example")) == [
        "billing contact changed from ap@test.example to ar@test.example"]


def test_a_collector_note_after_the_draft_withdraws():
    notes = [{"invoice_id": "INV-1", "touch_date": "2026-10-26", "collector": "Grace", "note": "Paying Friday"}]
    assert reasons(row(), notes=notes) == ["a person touched it on 2026-10-26 via collector sheet (Grace)"]


def test_missing_or_dequeued_invoice_withdraws():
    assert reasons(None) == ["the invoice is no longer in the export"]
    assert reasons(row(), queued=()) == ["it is no longer in the agent's queue"]


def test_revalidate_writes_sidecars_and_skips_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        actions = Path(tmp)
        for iid, state in (("INV-1", "approved"), ("INV-2", "rejected")):
            folder = actions / iid
            folder.mkdir()
            (folder / "status.json").write_text(json.dumps({"state": state, "action_id": f"{iid}:x:2026-10-25"}))
            (folder / "queue_item.json").write_text(json.dumps({**DRAFTED, "invoice_id": iid}))
        invoices = {"INV-1": row(status="paid", amount_paid="120.00")}
        report = revalidate.revalidate(actions, invoices, [], {}, {"as_of": "2026-10-27", "items": []},
                                       now=datetime(2026, 10, 27, 9))
        side = json.loads((actions / "INV-1" / "revalidation.json").read_text())
        assert not (actions / "INV-2" / "revalidation.json").exists()
        status = json.loads((actions / "INV-1" / "status.json").read_text())
    assert report["checked"] == 1 and report["withdrawn_approved"] == 1
    assert side["verdict"] == "withdraw" and side["drafted_from_snapshot"] == "2026-10-25"
    assert status["state"] == "approved"


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
