"""Tests for agent/actions.py and agent/review.py against the fixture queue. No model needed.

    python3 -m unittest agent/test_actions.py

The model-path tests run a fake OpenAI-compatible endpoint in-process, so the real HTTP code runs.
"""
import io
import json
import shutil
import socketserver
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import actions  # noqa: E402
import review  # noqa: E402

FIXTURE = HERE / "fixtures" / "queue.sample.json"
EXPECTED = json.loads((HERE / "fixtures" / "expected_actions.json").read_text())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="collections-test-"))
        self.saved = (actions.OUT_DIR, actions.CRM_PATH)
        actions.configure_paths(self.tmp / "out", self.tmp / "crm.sqlite")
        self.queue = json.loads(FIXTURE.read_text())

    def tearDown(self):
        actions.configure_paths(*self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_actions(self, *args, queue=None):
        path = FIXTURE
        if queue is not None:
            path = self.tmp / "queue.json"
            path.write_text(json.dumps(queue))
        out = io.StringIO()
        with redirect_stdout(out):
            code = actions.main(["--queue", str(path), "--no-post", *args])
        return code, out.getvalue()

    def review(self, *args):
        with redirect_stdout(io.StringIO()):
            return review.main(list(args))

    def status(self, invoice_id):
        return json.loads((actions.ACTIONS_DIR / invoice_id / "status.json").read_text())

    def folders(self):
        if not actions.ACTIONS_DIR.exists():
            return []
        return sorted(p.name for p in actions.ACTIONS_DIR.iterdir() if p.is_dir())


class DryRunTest(Base):
    def test_six_packages_match_expected_output(self):
        code, _ = self.run_actions("--dry-run", "--limit", "6")
        self.assertEqual(code, 0)
        self.assertEqual(self.folders(), sorted(EXPECTED["packages"]))
        for invoice_id, exp in EXPECTED["packages"].items():
            folder = actions.ACTIONS_DIR / invoice_id
            self.assertEqual(sorted(p.name for p in folder.iterdir() if p.is_file()), sorted(exp["files"]))
            status = self.status(invoice_id)
            self.assertEqual((status["state"], status["by"], status["action_id"]), ("drafted", "", exp["action_id"]))
            self.assertEqual(status["drafted_at"], status["at"])
            case = json.loads((folder / "salesforce_case.json").read_text())
            self.assertEqual(list(case)[:7], ["invoice_id", "case_type", "summary", "next_action",
                                              "next_action_date", "drafted_at", "model"])
            self.assertEqual((case["case_type"], case["action_id"], case["model"]), (exp["case_type"], exp["action_id"], "dry-run"))
            email = (folder / "customer_email.md").read_text().split("\n")
            self.assertIn(invoice_id, email[0])
            self.assertEqual(email[1], "")
            self.assertLess(len((folder / "customer_slack.md").read_text().strip()), 600)
            for name in exp["files"]:
                if name.endswith(".md"):
                    text = (folder / name).read_text()
                    self.assertNotIn("\u2014", text)
                    self.assertNotIn("finds you well", text.lower())
            if exp["bucket"] == "writeoff":
                memo = (folder / "writeoff_memo.md").read_text()
                self.assertIn("write_off_max_amount ($25.00)", memo)
                self.assertIn("min_days_past_due (30)", memo)
                self.assertTrue(memo.strip().splitlines()[-1].startswith("Approver:"))
        with closing(sqlite3.connect(str(actions.CRM_PATH))) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 6)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cases WHERE state = 'drafted'").fetchone()[0], 6)
        runs = json.loads(actions.SUMMARY_PATH.read_text())
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual((run["drafted"], run["model"], run["snapshot_id"]), (6, "dry-run", "2026-10-25T1430"))
        self.assertEqual({b: v["drafted"] for b, v in run["by_bucket"].items()}, {"agent": 3, "writeoff": 2, "escalate": 1})
        self.assertAlmostEqual(run["drafted_dollars"], 1177.51)

    def test_never_drafted_first_by_amount(self):
        self.run_actions("--dry-run", "--limit", "3")
        self.assertEqual(self.folders(), ["INV-100417", "INV-102988", "INV-107752"])

    def test_rerun_and_new_snapshot_skip_fresh_packages(self):
        self.run_actions("--dry-run", "--limit", "6")
        _, out = self.run_actions("--dry-run")
        self.assertIn("run done: drafted 0", out)
        _, out = self.run_actions("--dry-run", queue=dict(self.queue, snapshot_id="2026-10-25T1630"))
        self.assertIn("run done: drafted 0", out)
        self.assertEqual(self.status("INV-100417")["action_id"], "INV-100417:outreach:2026-10-25T1430")

    def test_rejected_and_stale_packages_draft_again(self):
        self.run_actions("--dry-run", "--limit", "6")
        self.review("reject", "INV-100417", "--by", "test")
        path = actions.ACTIONS_DIR / "INV-102988" / "status.json"
        data = json.loads(path.read_text())
        data["drafted_at"] = data["at"] = (datetime.now().astimezone() - timedelta(days=9)).isoformat(timespec="seconds")
        path.write_text(json.dumps(data))
        _, out = self.run_actions("--dry-run", queue=dict(self.queue, snapshot_id="2026-11-03T0900"))
        self.assertIn("run done: drafted 2", out)
        self.assertEqual(self.status("INV-100417")["state"], "drafted")
        self.assertEqual(self.status("INV-100417")["action_id"], "INV-100417:outreach:2026-11-03T0900")
        self.assertEqual(self.status("INV-102988")["action_id"], "INV-102988:outreach:2026-11-03T0900")
        self.assertEqual(self.status("INV-109311")["action_id"], "INV-109311:outreach:2026-10-25T1430")
        self.assertEqual(len(list((actions.ACTIONS_DIR / "INV-100417" / "history").iterdir())), 1)

    def test_bucket_change_drafts_again(self):
        self.run_actions("--dry-run", "--limit", "6")
        queue = json.loads(json.dumps(self.queue))
        for item in queue["items"]:
            if item["invoice_id"] == "INV-109311":
                item["bucket"] = "escalate"
                item["reasons"].append("disputed")
        _, out = self.run_actions("--dry-run", queue=queue)
        self.assertIn("run done: drafted 1", out)
        self.assertEqual(self.status("INV-109311")["action_id"], "INV-109311:escalate:2026-10-25T1430")

    def test_approve_updates_status_and_crm(self):
        self.run_actions("--dry-run", "--limit", "6")
        before = self.status("INV-104233")
        self.review("approve", "INV-104233", "--by", "Mohammed")
        after = self.status("INV-104233")
        self.assertEqual((after["state"], after["by"]), ("approved", "Mohammed"))
        self.assertEqual((after["action_id"], after["drafted_at"]), (before["action_id"], before["drafted_at"]))
        with closing(sqlite3.connect(str(actions.CRM_PATH))) as con:
            state = con.execute("SELECT state FROM cases WHERE invoice_id = 'INV-104233' ORDER BY case_id DESC").fetchone()[0]
        self.assertEqual(state, "approved")
        counts = actions.review_counts()
        self.assertEqual((counts["approved"], counts["written_off_dollars"]), (1, 18.4))


class ParseTest(unittest.TestCase):
    def test_extract_json_object(self):
        self.assertEqual(actions.extract_json_object('<think>plan</think>\n```json\n{"a": "b"}\n```'), {"a": "b"})
        self.assertEqual(actions.extract_json_object('Here it is: {"a": "one\ntwo"} done'), {"a": "one\ntwo"})
        self.assertIsNone(actions.extract_json_object("no object here"))

    def test_touch_claims_follow_the_facts(self):
        queue = json.loads(FIXTURE.read_text())
        meta = {"as_of": datetime(2026, 10, 25).date(), "snapshot_id": "x"}
        facts = {i["invoice_id"]: actions.build_facts(i, meta, actions.load_policy(queue)) for i in queue["items"]}
        note = "Hi Marcus,\n\nINV-104233 is 85 days past due. There is no touch on record. We propose a write-off."
        self.assertIn("The last touch was on September 2, 2026 (email, Salesforce).", actions.fix_touch_claims(note, facts["INV-104233"]))
        note = "Hi Elena,\n\nINV-118904 is 132 days past due. The last touch was no touch on record.\nWe propose a write-off."
        self.assertIn("past due. There is no touch on record.\nWe", actions.fix_touch_claims(note, facts["INV-118904"]))

    def test_clean_text(self):
        self.assertEqual(actions.clean_text("Hi,\n\nI hope this email finds you well.\n\nPay \u2014 please."), "Hi,\n\nPay, please.")


class FakeModel(BaseHTTPRequestHandler):
    replies, seen = [], []

    def do_POST(self):
        FakeModel.seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        status, content = FakeModel.replies.pop(0)
        payload = {"choices": [{"message": {"role": "assistant", "content": content}}]} if status == 200 else {"error": content}
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FakeModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # skip the reverse DNS lookup HTTPServer does on bind
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class ModelPathTest(Base):
    def setUp(self):
        super().setUp()
        self.server = FakeModelServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = self.tmp / "model.env"
        self.env.write_text(f"MODEL_BASE_URL=http://127.0.0.1:{self.server.server_port}/v1\nMODEL_NAME=qwen3:8b\n")
        FakeModel.replies, FakeModel.seen = [], []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def read(self, invoice_id, name):
        return (actions.ACTIONS_DIR / invoice_id / name).read_text()

    def test_one_call_json_with_fences_and_reasoning(self):
        drafts = {
            "customer_email_subject": "Invoice INV-100417 for $482.00 is 62 days past due",
            "customer_email_body": "Hi Dana,\n\nI hope this email finds you well. Invoice INV-100417 for $482.00 was due on "
                                   "August 24, 2026 \u2014 now 62 days past due.\n\nThank you,\nAccounts Receivable",
            "customer_slack": "Hi Dana, invoice INV-100417 for $482.00 is 62 days past due. Could you send payment or a pay date by November 2, 2026?",
            "internal_note": "Hi Priya,\n\nLakeshore Physical Therapy's invoice INV-100417 is 62 days past due.\n\nQuestion: Is Dana Lee still the AP contact?",
        }
        FakeModel.replies = [(200, "<think>\n\n</think>\n```json\n" + json.dumps(drafts) + "\n```")]
        code, _ = self.run_actions("--invoice", "INV-100417", "--model-env", str(self.env))
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeModel.seen), 1)
        request = FakeModel.seen[0]
        self.assertEqual((request["model"], request["temperature"]), ("qwen3:8b", 0.2))
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False})
        self.assertTrue(request["messages"][1]["content"].endswith("/no_think"))
        self.assertNotIn("Shape when the bucket is writeoff", request["messages"][0]["content"])
        email = self.read("INV-100417", "customer_email.md")
        self.assertTrue(email.startswith("Invoice INV-100417 for $482.00 is 62 days past due\n\nHi Dana,\n\nInvoice INV-100417"))
        self.assertNotIn("\u2014", email)
        self.assertEqual(json.loads(self.read("INV-100417", "salesforce_case.json"))["model"], "qwen3:8b")

    def test_json_mode_refused_then_one_call_per_artifact(self):
        FakeModel.replies = [
            (400, "response_format is not supported"),
            (200, "Sure, here are the drafts you asked for."),
            (200, "Subject: Invoice INV-104233 for $18.40\n\nHi Alicia,\n\nWe have closed the $18.40 balance on invoice "
                  "INV-104233, so no payment is needed.\n\nThank you,\nAccounts Receivable"),
            (200, "Hi Alicia, we have closed the $18.40 balance on invoice INV-104233, so no payment is needed."),
            (200, "Hi Marcus,\n\nBrightline Dental Group's invoice INV-104233 for $18.40 is 85 days past due.\n\nQuestion: Is a credit pending?"),
            (200, "Write-off memo: INV-104233, Brightline Dental Group\nAmount: $18.40\nReason: Bank fee on a wire."),
        ]
        code, _ = self.run_actions("--invoice", "INV-104233", "--model-env", str(self.env))
        self.assertEqual(code, 0)
        self.assertEqual(len(FakeModel.seen), 6)
        self.assertNotIn("response_format", FakeModel.seen[1])
        self.assertNotIn("reasoning_effort", FakeModel.seen[1])
        self.assertEqual(FakeModel.seen[1]["messages"][1]["content"].count("/no_think"), 1)
        self.assertTrue(self.read("INV-104233", "customer_email.md").startswith("Invoice INV-104233 for $18.40\n\nHi Alicia,"))
        memo = self.read("INV-104233", "writeoff_memo.md").strip().splitlines()
        self.assertTrue(any("write_off_max_amount ($25.00)" in line for line in memo))
        self.assertTrue(memo[-1].startswith("Approver:"))

    def test_dry_run_package_is_replaced_by_a_model_run(self):
        self.run_actions("--dry-run", "--invoice", "INV-109311")
        drafts = {
            "customer_email_subject": "Invoice INV-109311 for $129.99 is 35 days past due",
            "customer_email_body": "Hi Theo,\n\nInvoice INV-109311 for $129.99 was due on September 20, 2026.\n\nThank you,\nAccounts Receivable",
            "customer_slack": "Hi Theo, invoice INV-109311 for $129.99 is 35 days past due.",
            "internal_note": "Hi Priya,\n\nBluefin Marine Supply's invoice INV-109311 is 35 days past due.\n\nQuestion: Right contact?",
        }
        FakeModel.replies = [(200, json.dumps(drafts))]
        _, out = self.run_actions("--invoice", "INV-109311", "--model-env", str(self.env))
        self.assertIn("run done: drafted 1", out)
        self.assertEqual(json.loads(self.read("INV-109311", "salesforce_case.json"))["model"], "qwen3:8b")

    def test_unreachable_endpoint_stops_cleanly(self):
        down = self.tmp / "down.env"
        down.write_text("MODEL_BASE_URL=http://127.0.0.1:9/v1\nMODEL_NAME=qwen3:8b\n")
        code, out = self.run_actions("--limit", "3", "--model-env", str(down))
        self.assertEqual(code, 1)
        self.assertIn("stopped: model endpoint unreachable", out)
        self.assertEqual(self.folders(), [])
        run = json.loads(actions.SUMMARY_PATH.read_text())[-1]
        self.assertEqual(run["drafted"], 0)
        self.assertIn("stopped", run)


if __name__ == "__main__":
    unittest.main()
