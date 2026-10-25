"""python -m pytest disputes/   or   python disputes/test_disputes.py"""
import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disputes  # noqa: E402


def item(iid="INV-1", balance=120.0, dpd=45, notes=(), slack=None, bucket="escalate", account="ACC-1"):
    return {
        "invoice_id": iid, "bucket": bucket, "account_id": account, "account_name": "Test Labs",
        "account_owner": "Elena Sato", "owner_email": "elenasato@ourco.example",
        "billing_contact_email": "ap@testlabs.example", "balance": balance, "days_past_due": dpd,
        "status": "disputed",
        "sheet_notes": [{"date": d, "collector": "Ana", "note": n} for d, n in notes],
        "slack_thread": slack,
    }


def queue(*items):
    return {"as_of": "2026-10-23", "items": list(items)}  # a Friday


def one(i, concede_max=25.0):
    return disputes.run(queue(i), concede_max)[0]


def test_only_escalate_items_become_disputes():
    packets = disputes.run(queue(item("INV-1"), item("INV-2", bucket="agent")), 25.0)
    assert [p["invoice_id"] for p in packets] == ["INV-1"]


def test_reason_comes_from_notes_and_routes_to_the_right_team():
    p = one(item(notes=[("2026-10-01", "Customer disputes usage overage, waiting on deal desk")]))
    assert p["reason"] == "usage_overage"
    assert p["route_email"] == "dealdesk@ourco.example"
    assert p["owner_note"]["cc"] == "elenasato@ourco.example"
    assert p["evidence"][0]["text"].startswith("Customer disputes usage")


def test_slack_counts_as_evidence():
    slack = {"channel": "#collections", "messages": [{"date": "2026-10-10", "user": "Ana",
                                                      "text": "Customer says INV-1 was paid but I can't find the remittance."}]}
    p = one(item(slack=slack))
    assert p["reason"] == "already_paid"
    assert p["route_to"] == "billing_ops"


def test_newest_matching_line_decides_the_reason():
    p = one(item(notes=[("2026-10-01", "Customer says this was paid"), ("2026-10-02", "Customer disputes usage overage")]))
    assert p["reason"] == "usage_overage"
    p = one(item(notes=[("2026-10-01", "Spoke with AP, payment scheduled for next run"),
                        ("2026-10-05", "Customer says this was paid, asked for remittance")]))
    assert p["reason"] == "already_paid"


def test_whole_words_only_and_negation_does_not_count_as_paid():
    assert one(item(notes=[("2026-10-01", "Insurance coverage question")]))["reason"] == "no_reason"
    assert one(item(notes=[("2026-10-01", "Customer says it wasn't paid, no remittance yet")]))["reason"] == "no_reason"


def test_missing_fields_do_not_crash():
    i = item(notes=[("2026-10-01", "Left voicemail")])
    i["account_owner"], i["owner_email"] = "", None
    del i["sheet_notes"][0]["collector"]
    p = one(i)
    assert p["route_email"] == "billing-ops@ourco.example"
    assert p["owner_note"]["body"].startswith("Hi team,")


def test_payment_promise_marks_a_stale_flag():
    p = one(item(notes=[("2026-10-01", "AP says payment run is on the 15th")]))
    assert p["reason"] == "payment_promised"
    assert "stale" in p["resolution"]


def test_no_notes_means_ask_the_customer():
    p = one(item(balance=120.0))
    assert p["reason"] == "no_reason"
    assert "reply with a short note" in p["customer_ack"]["body"]
    assert p["route_email"] == "elenasato@ourco.example"
    assert p["owner_note"]["cc"] is None


def test_small_dispute_recommends_concede_without_asking_for_reason():
    p = one(item(balance=25.0))
    assert p["concede_recommended"] is True
    assert "credit memo" in p["resolution"]
    assert "reply with a short note" not in p["customer_ack"]["body"]
    assert one(item(balance=25.01))["concede_recommended"] is False


def test_never_concede_when_customer_says_it_was_paid():
    p = one(item(balance=10.0, notes=[("2026-10-01", "Customer says this was paid, asked for remittance")]))
    assert p["concede_recommended"] is False


def test_priority_and_respond_by_skip_weekends():
    p1 = one(item(balance=250.0))
    assert p1["priority"] == "P1"
    assert p1["respond_by"] == "2026-10-27"  # Friday + 2 business days
    assert one(item(balance=100.0, dpd=90))["priority"] == "P1"
    assert one(item(balance=100.0, dpd=60))["priority"] == "P2"
    assert one(item(balance=100.0, dpd=59))["priority"] == "P3"
    assert disputes.add_business_days(date(2026, 10, 23), 10) == date(2026, 11, 6)


def test_customer_ack_never_asks_for_payment_or_promises_a_hold():
    p = one(item())
    body = p["customer_ack"]["body"].lower()
    assert "won't send payment reminders" in body
    assert "please pay" not in body and "$" not in body and "nothing you need to pay" not in body
    assert "October 27" in one(item(balance=300.0))["customer_ack"]["body"]


def test_customer_facing_dates_have_no_leading_zero():
    p = one(item(balance=100.0, dpd=10))  # P3: 10 business days from Fri Oct 23
    assert "November 6," in p["customer_ack"]["body"]


def test_other_queued_invoices_on_the_account_are_mentioned():
    packets = disputes.run(queue(item("INV-1"), item("INV-2", bucket="agent", balance=80.0)), 25.0)
    assert packets[0]["account_other_queued"] == {"count": 1, "dollars": 80.0}
    assert "1 other invoice in the queue" in packets[0]["owner_note"]["body"]


def test_write_produces_files_csv_and_summary_and_drops_only_its_own_stale_packets():
    q = queue(item("INV-1", balance=300.0), item("INV-2", balance=10.0))
    packets = disputes.run(q, 25.0)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "INV-OLD.json").write_text('{"generated_by": "disputes"}')
        (out / "INV-OTHER.json").write_text('{"status": "approved"}')
        disputes.write(packets, disputes.summarize(packets, q), out)
        assert sorted(p.name for p in out.glob("INV-*.json")) == ["INV-1.json", "INV-2.json", "INV-OTHER.json"]
        rows = list(csv.DictReader(open(out / "salesforce_cases.csv", encoding="utf-8")))
        assert [r["Case_External_Id__c"] for r in rows] == ["DSP-INV-1", "DSP-INV-2"]
        summary = json.loads((out / "summary.json").read_text())
    assert summary["disputes"] == 2
    assert summary["concede_recommended"] == {"count": 1, "dollars": 10.0}
    assert summary["by_priority"]["P1"]["count"] == 1


def test_csv_cells_cannot_become_formulas():
    assert disputes.cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert disputes.cell("120 days") == "120 days"


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
