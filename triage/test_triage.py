"""python -m pytest triage/   or   python triage/test_triage.py"""
import csv
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402

AS_OF = date(2026, 10, 25)
POLICY = {
    "min_days_past_due": 30,
    "stale_touch_days": 8,
    "agent_max_amount": 500.0,
    "write_off_max_amount": 5.0,
    "minutes_per_touch": 6.0,
}


def ago(days):
    return (AS_OF - timedelta(days=days)).isoformat()


def invoice(iid="INV-1", amount="120.00", paid="0.00", status="open", dpd=45, touched=None, dispute="false"):
    return {
        "invoice_id": iid, "account_id": "ACC-1", "account_name": "Test Labs", "account_owner": "Owner One",
        "owner_email": "owner@ourco.example", "billing_contact_email": "ap@testlabs.example", "currency": "USD",
        "amount": amount, "amount_paid": paid, "status": status, "issue_date": ago(dpd + 30), "due_date": ago(dpd),
        "last_touch_date": ago(touched) if touched is not None else "", "dispute_flag": dispute,
    }


def run(*invoices, notes=(), slack=None):
    queue = triage.triage({i["invoice_id"]: i for i in invoices}, list(notes), slack or {}, POLICY, AS_OF)
    return {item["invoice_id"]: item for item in queue["items"]}, queue


def test_exactly_30_days_past_due_queues():
    items, _ = run(invoice("A", dpd=30), invoice("B", dpd=29))
    assert "A" in items
    assert "B" not in items


def test_exactly_8_days_since_touch_queues():
    items, _ = run(invoice("A", touched=8), invoice("B", touched=7))
    assert "A" in items
    assert items["A"]["days_since_touch"] == 8
    assert "B" not in items


def test_never_touched_queues():
    items, _ = run(invoice("A"))
    assert items["A"]["never_touched"] is True


def test_agent_max_boundary():
    items, _ = run(invoice("A", amount="500.00"), invoice("B", amount="500.01"))
    assert "A" in items
    assert "B" not in items


def test_cents_invoice_is_written_off():
    items, _ = run(invoice("A", amount="0.37"))
    assert items["A"]["bucket"] == "writeoff"


def test_disputed_small_invoice_escalates():
    items, _ = run(invoice("A", amount="200.00", status="disputed"), invoice("B", amount="80.00", dispute="true"))
    assert items["A"]["bucket"] == "escalate"
    assert items["B"]["bucket"] == "escalate"


def test_plain_small_invoice_goes_to_agent():
    items, _ = run(invoice("A", amount="42.10"))
    assert items["A"]["bucket"] == "agent"


def test_paid_invoice_never_queues():
    items, queue = run(invoice("A", amount="0.37", status="paid", paid="0.37", dpd=180))
    assert items == {}
    assert queue["totals"]["open"] == 0


def test_partial_payment_uses_remaining_balance():
    items, _ = run(invoice("A", amount="900.00", paid="600.00", status="partially_paid"))
    assert items["A"]["balance"] == 300.0


def test_conflict_recorded_and_latest_touch_wins():
    notes = [{"invoice_id": "A", "collector": "Ana", "touch_date": ago(2), "note": "Spoke with AP"}]
    items, queue = run(invoice("A", touched=21), notes=notes)
    assert "A" not in items
    assert queue["conflicts"] == [{"invoice_id": "A", "touches": {"salesforce": ago(21), "sheet": ago(2)}, "gap_days": 19}]


def test_stale_conflict_still_queues_with_detail():
    notes = [{"invoice_id": "A", "collector": "Ana", "touch_date": ago(12), "note": "Left voicemail"}]
    items, _ = run(invoice("A", touched=40), notes=notes)
    assert items["A"]["last_touch_source"] == "sheet"
    assert items["A"]["conflict"]["gap_days"] == 28


def test_slack_counts_as_a_touch():
    slack = {"A": {"invoice_id": "A", "channel": "#collections", "thread_ts": "1", "last_message_date": ago(3), "messages": []}}
    items, _ = run(invoice("A"), slack=slack)
    assert items == {}


def test_policy_parses_without_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "policy.yaml"
        path.write_text("min_days_past_due: 30\nstale_touch_days: 8  # days\nagent_max_amount: 500\n"
                        "write_off_max_amount: 5\nminutes_per_touch: 6\n", encoding="utf-8")
        saved = sys.modules.get("yaml")
        sys.modules["yaml"] = None
        try:
            policy = triage.load_policy(path)
        finally:
            if saved is None:
                sys.modules.pop("yaml", None)
            else:
                sys.modules["yaml"] = saved
    assert policy == POLICY


def write_bundle(folder, rows, manifest):
    folder.mkdir(parents=True)
    with open(folder / "salesforce_invoices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_delta_in_inbox_overlays_exports():
    with tempfile.TemporaryDirectory() as tmp:
        exports, inbox = Path(tmp) / "exports", Path(tmp) / "inbox"
        write_bundle(exports, [invoice("A"), invoice("B")], {"kind": "full", "as_of": AS_OF.isoformat()})
        write_bundle(inbox, [invoice("B", status="paid", paid="120.00")], {"kind": "delta", "as_of": ago(-2)})
        folders = triage.pick_sources(inbox, exports)
        invoices, notes, slack, as_of = triage.load_sources(folders)
    assert folders == [exports, inbox]
    assert invoices["B"]["status"] == "paid"
    assert as_of == AS_OF + timedelta(days=2)


def test_malformed_rows_are_skipped_not_fatal():
    bad = invoice("B", amount="")
    notes = [{"invoice_id": "A", "collector": "Ana", "touch_date": "last tuesday", "note": "?"}]
    items, queue = run(invoice("A"), bad, notes=notes)
    assert "A" in items
    assert queue["totals"]["malformed_rows_skipped"] == 2


def test_zero_balance_on_unpaid_status_is_not_written_off():
    items, _ = run(invoice("A", amount="50.00", paid="50.00"))
    assert items == {}


def test_dispute_flag_spellings():
    items, _ = run(invoice("A", dispute="Yes"), invoice("B", dispute="1"))
    assert items["A"]["bucket"] == items["B"]["bucket"] == "escalate"


def test_bom_csv_from_excel_loads():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "inbox"
        write_bundle(folder, [invoice("A")], {"kind": "full", "as_of": AS_OF.isoformat()})
        path = folder / "salesforce_invoices.csv"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        _, invoices, _, _ = triage.load_bundle(folder)
    assert "A" in invoices


def test_inbox_with_only_a_subfolder_falls_back_to_exports():
    with tempfile.TemporaryDirectory() as tmp:
        exports, inbox = Path(tmp) / "exports", Path(tmp) / "inbox"
        (inbox / "delta").mkdir(parents=True)
        assert triage.pick_sources(inbox, exports) == [exports]


def test_empty_inbox_falls_back_to_exports():
    with tempfile.TemporaryDirectory() as tmp:
        exports, inbox = Path(tmp) / "exports", Path(tmp) / "inbox"
        inbox.mkdir()
        (inbox / ".gitkeep").touch()
        assert triage.pick_sources(inbox, exports) == [exports]


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
