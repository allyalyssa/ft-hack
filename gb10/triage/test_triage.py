"""Tests for triage/triage.py. Plain unittest, no fixtures on disk.

    python3 -m unittest triage/test_triage.py
"""
import importlib.util
import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("triage_under_test", HERE / "triage.py")
tri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tri)

AS_OF = date(2026, 10, 25)
POLICY_TEXT = """as_of: 2026-10-25
min_days_past_due: 30
stale_touch_days: 8
write_off_max_amount: 25.00
agent_max_amount: 500.00
currency: USD
"""
POLICY = tri.parse_policy(POLICY_TEXT)
CONTRACT_TOTALS = {"invoices", "open", "under_agent_max", "queued", "writeoff", "agent",
                   "escalate", "dollars_queued"}
CONTRACT_ITEM = {"invoice_id", "bucket", "reasons", "invoice", "last_touch", "notes", "slack", "conflicts"}


def day(days_ago):
    return (AS_OF - timedelta(days=days_ago)).isoformat()


def invoice(invoice_id="INV-00001", amount="120.00", status="open", past_due=45, touched=None, channel="email"):
    return {
        "invoice_id": invoice_id, "account_id": "0018b000TEST001", "account_name": "Test Supply LLC",
        "account_owner": "Priya Raman", "account_owner_email": "priya.raman@example.com",
        "amount": amount, "currency": "USD", "invoice_date": day(past_due + 30),
        "due_date": day(past_due), "status": status,
        "last_touch_date": "" if touched is None else day(touched),
        "last_touch_channel": "" if touched is None else channel,
        "contact_name": "Maria Chen", "contact_email": "maria.chen@testsupply.example.com",
        "contact_slack": "@maria.chen",
    }


def note(invoice_id, days_ago, text="Emailed reminder, no reply yet.", dispute=False):
    return {"invoice_id": invoice_id, "collector": "Jordan Reyes", "note": text,
            "note_date": day(days_ago), "promise_to_pay_date": "",
            "dispute_flag": "true" if dispute else "false"}


def thread(invoice_id, *days_ago):
    return {"invoice_id": invoice_id, "channel": "#ar-collections",
            "messages": [{"ts": day(d) + "T10:00:00", "user": "jreyes", "text": "Any update?"}
                         for d in days_ago]}


def triage(invoices, notes=(), threads=(), policy=POLICY):
    items, counts = tri.triage(policy, invoices, tri.index_notes(notes), tri.index_slack(threads))
    return {item["invoice_id"]: item for item in items}, items, counts


def write_export(folder, invoices, notes=(), threads=(), policy=POLICY_TEXT):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "salesforce_invoices.csv", "w", newline="") as f:
        writer = tri.csv.DictWriter(f, fieldnames=list(invoices[0]))
        writer.writeheader()
        writer.writerows(invoices)
    with open(folder / "collector_sheet.csv", "w", newline="") as f:
        writer = tri.csv.DictWriter(f, fieldnames=["invoice_id", "collector", "note", "note_date",
                                                  "promise_to_pay_date", "dispute_flag"])
        writer.writeheader()
        writer.writerows(notes)
    (folder / "slack_threads.json").write_text(json.dumps(list(threads)))
    (folder / "policy.yaml").write_text(policy)


class QueueRule(unittest.TestCase):
    def test_exactly_30_days_past_due_queues(self):
        queued, _, _ = triage([invoice(past_due=30)])
        self.assertIn("past_due_30_days", queued["INV-00001"]["reasons"])

    def test_29_days_past_due_does_not_queue(self):
        queued, _, _ = triage([invoice(past_due=29)])
        self.assertEqual(queued, {})

    def test_exactly_8_days_since_touch_queues(self):
        queued, _, _ = triage([invoice(touched=8)])
        self.assertEqual(queued["INV-00001"]["days_since_touch"], 8)
        self.assertIn("no_touch_8_days", queued["INV-00001"]["reasons"])

    def test_7_days_since_touch_does_not_queue(self):
        queued, _, _ = triage([invoice(touched=7)])
        self.assertEqual(queued, {})

    def test_never_touched_queues(self):
        item = triage([invoice(touched=None)])[0]["INV-00001"]
        self.assertIn("never_touched", item["reasons"])
        self.assertIsNone(item["days_since_touch"])
        self.assertEqual(item["last_touch"], {"date": None, "channel": None, "source": None})

    def test_paid_never_queues(self):
        queued, _, counts = triage([invoice(status="paid", amount="40.00", past_due=150)])
        self.assertEqual(queued, {})
        self.assertEqual(counts["open"], 0)

    def test_amount_over_agent_max_never_queues(self):
        queued, _, _ = triage([invoice("INV-1", amount="500.01", past_due=170),
                               invoice("INV-2", amount="140000.00", past_due=90, status="disputed")])
        self.assertEqual(queued, {})

    def test_amount_exactly_agent_max_queues(self):
        queued, _, _ = triage([invoice(amount="500.00")])
        self.assertEqual(queued["INV-00001"]["bucket"], "agent")

    def test_zero_and_credit_balances_never_queue(self):
        queued, _, _ = triage([invoice("INV-1", amount="0.00"), invoice("INV-2", amount="-12.50")])
        self.assertEqual(queued, {})


class Buckets(unittest.TestCase):
    def test_cents_invoice_goes_to_writeoff(self):
        item = triage([invoice(amount="0.37", past_due=95)])[0]["INV-00001"]
        self.assertEqual(item["bucket"], "writeoff")
        self.assertIn("amount_under_25", item["reasons"])

    def test_writeoff_boundary_is_inclusive(self):
        queued, _, _ = triage([invoice("INV-1", amount="25.00"), invoice("INV-2", amount="25.01")])
        self.assertEqual(queued["INV-1"]["bucket"], "writeoff")
        self.assertEqual(queued["INV-2"]["bucket"], "agent")

    def test_disputed_small_invoice_escalates(self):
        item = triage([invoice(amount="180.00", status="disputed")])[0]["INV-00001"]
        self.assertEqual(item["bucket"], "escalate")
        self.assertIn("status_disputed", item["reasons"])

    def test_sheet_dispute_flag_escalates_and_is_a_conflict(self):
        item = triage([invoice(amount="180.00", touched=12)],
                      [note("INV-00001", 12, "Spoke with Maria, they dispute the overage.", dispute=True)])[0]["INV-00001"]
        self.assertEqual(item["bucket"], "escalate")
        self.assertIn("sheet_dispute_flag", item["reasons"])
        self.assertEqual(item["conflicts"], ["sheet flags a dispute, salesforce status is open"])

    def test_writeoff_wins_over_dispute_for_tiny_amounts(self):
        item = triage([invoice(amount="4.10", status="disputed")])[0]["INV-00001"]
        self.assertEqual(item["bucket"], "writeoff")

    def test_queue_order_is_bucket_then_amount_descending(self):
        _, items, _ = triage([
            invoice("INV-A", amount="60.00"), invoice("INV-B", amount="300.00"),
            invoice("INV-C", amount="200.00", status="disputed"), invoice("INV-D", amount="0.37"),
            invoice("INV-E", amount="12.00"),
        ])
        self.assertEqual([i["invoice_id"] for i in items], ["INV-E", "INV-D", "INV-B", "INV-A", "INV-C"])


class LatestTouch(unittest.TestCase):
    def test_conflict_when_sheet_date_differs_from_salesforce(self):
        item = triage([invoice(touched=21)], [note("INV-00001", 10, "Called Maria, left voicemail.")])[0]["INV-00001"]
        self.assertEqual(item["conflicts"], [f"sheet says touched {day(10)}, salesforce says {day(21)}"])
        self.assertEqual(item["last_touch"], {"date": day(10), "channel": "phone", "source": "sheet"})
        self.assertEqual(item["days_since_touch"], 10)

    def test_conflict_when_salesforce_has_no_touch(self):
        item = triage([invoice(touched=None)], [note("INV-00001", 15)])[0]["INV-00001"]
        self.assertEqual(item["conflicts"], [f"sheet says touched {day(15)}, salesforce says never touched"])

    def test_no_conflict_when_sheet_matches_salesforce(self):
        item = triage([invoice(touched=15)], [note("INV-00001", 15)])[0]["INV-00001"]
        self.assertEqual(item["conflicts"], [])
        self.assertEqual(item["last_touch"]["source"], "salesforce")

    def test_recent_sheet_note_holds_back_and_counts_the_conflict(self):
        queued, _, counts = triage([invoice(touched=21)], [note("INV-00001", 3)])
        self.assertEqual(queued, {})
        self.assertEqual((counts["conflicts"], counts["held_back"]), (1, 1))

    def test_recent_slack_message_holds_invoice_back(self):
        queued, _, counts = triage([invoice(touched=20)], threads=[thread("INV-00001", 2)])
        self.assertEqual(queued, {})
        self.assertEqual(counts["held_back"], 1)

    def test_newest_source_wins(self):
        item = triage([invoice(touched=30)], [note("INV-00001", 20)], [thread("INV-00001", 15, 12)])[0]["INV-00001"]
        self.assertEqual(item["last_touch"], {"date": day(12), "channel": "slack", "source": "slack"})
        self.assertEqual(len(item["slack"]), 2)


class Policy(unittest.TestCase):
    def test_parser_reads_flat_lines_with_comments_and_quotes(self):
        policy = tri.parse_policy("# client policy\nas_of: '2026-10-01'\nmin_days_past_due: 45  # longer\n"
                                  "agent_max_amount: \"250.00\"\n")
        self.assertEqual(policy["as_of"], date(2026, 10, 1))
        self.assertEqual(policy["min_days_past_due"], 45)
        self.assertEqual(policy["agent_max_amount"], 250.0)
        self.assertEqual(policy["stale_touch_days"], 8)  # default

    def test_policy_thresholds_drive_the_rule(self):
        policy = tri.parse_policy(POLICY_TEXT.replace("agent_max_amount: 500.00", "agent_max_amount: 100.00"))
        queued, _, _ = triage([invoice("INV-1", amount="100.00"), invoice("INV-2", amount="100.01")], policy=policy)
        self.assertEqual(list(queued), ["INV-1"])
        self.assertIn("amount_under_100", queued["INV-1"]["reasons"])


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_writes_contract_shaped_queue_and_headline(self):
        write_export(self.root / "in", [invoice("INV-1", amount="0.37"), invoice("INV-2"),
                                        invoice("INV-3", status="paid")], [note("INV-2", 9)])
        queue, headline, _ = tri.run(self.root / "in", self.root / "out")
        on_disk = json.loads((self.root / "out" / "queue.json").read_text())
        self.assertEqual(on_disk["items"], queue["items"])
        self.assertEqual(set(on_disk["totals"]), CONTRACT_TOTALS)
        self.assertEqual(on_disk["totals"]["dollars_queued"], 120.37)
        self.assertEqual(on_disk["snapshot_id"], "local")
        self.assertEqual(on_disk["as_of"], "2026-10-25")
        self.assertTrue(all(CONTRACT_ITEM <= set(item) for item in on_disk["items"]))
        saved = json.loads((self.root / "out" / "headline.json").read_text())
        self.assertEqual(saved["hours_per_week"], 0.2)  # 2 queued x 6 minutes
        self.assertEqual(saved["buckets"]["writeoff"], {"count": 1, "dollars": 0.37})
        self.assertEqual(headline, saved)

    def test_manifest_sets_snapshot_and_the_date_for_age_math(self):
        # 28 days past due on the policy date, 30 on the export date three days later.
        write_export(self.root / "in", [invoice(past_due=28)])
        (self.root / "in" / "manifest.json").write_text(json.dumps({
            "snapshot_id": "2026-10-27T1430", "exported_at": "2026-10-27T14:30:00",
            "source_files": ["salesforce_invoices.csv", "collector_sheet.csv", "slack_threads.json"]}))
        queue, headline, _ = tri.run(self.root / "in", self.root / "out")
        self.assertEqual((queue["snapshot_id"], queue["as_of"]), ("2026-10-27T1430", "2026-10-27"))
        self.assertEqual(headline["snapshot_id"], "2026-10-27T1430")
        self.assertEqual(queue["items"][0]["reasons"][0], "past_due_30_days")

    def test_approved_or_edited_agent_package_counts_as_a_touch(self):
        write_export(self.root / "in", [invoice("INV-A"), invoice("INV-B"), invoice("INV-C"),
                                        invoice("INV-D", touched=20)])
        packages = {"INV-A": ("approved", 2), "INV-B": ("drafted", 2), "INV-C": ("rejected", 2),
                    "INV-D": ("edited", 9)}
        for invoice_id, (state, days_ago) in packages.items():
            folder = self.root / "out" / "actions" / invoice_id
            folder.mkdir(parents=True)
            (folder / "status.json").write_text(json.dumps({  # the shape role C writes
                "state": state, "by": "collector", "at": day(days_ago) + "T14:35:12-07:00",
                "action_id": f"{invoice_id}:customer_email:2026-10-25T0600"}))
        queue, headline, _ = tri.run(self.root / "in", self.root / "out")
        queued = {item["invoice_id"]: item for item in queue["items"]}
        self.assertEqual(sorted(queued), ["INV-B", "INV-C", "INV-D"])
        self.assertEqual(queued["INV-D"]["last_touch"], {"date": day(9), "channel": "agent", "source": "agent"})
        self.assertEqual(headline["held_back_by_agent"], 1)

    def test_inbox_with_an_export_wins_over_exports(self):
        inbox, exports = self.root / "inbox", self.root / "exports"
        write_export(inbox, [invoice()])
        self.assertEqual(tri.pick_source(inbox=inbox, exports=exports), inbox)

    def test_empty_inbox_falls_back_to_exports(self):
        inbox, exports = self.root / "inbox", self.root / "exports"
        inbox.mkdir()
        (inbox / ".gitkeep").write_text("")
        self.assertEqual(tri.pick_source(inbox=inbox, exports=exports), exports)

    @unittest.skipUnless((tri.EXPORTS / tri.SALESFORCE).is_file(), "run python3 data/generate.py first")
    def test_full_export_triages_in_under_two_seconds(self):
        started = time.perf_counter()
        queue, _, _ = tri.run(tri.EXPORTS, self.root / "out")
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(queue["totals"]["invoices"], 20_000)


if __name__ == "__main__":
    unittest.main()
