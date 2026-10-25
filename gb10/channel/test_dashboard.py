"""Tests for the dashboard's JSON API and HTML routes. Stdlib unittest, no model, no network.

    python3 -m unittest channel/test_dashboard.py

Drafts five of the six fixture invoices with actions.py --dry-run into a temp folder, then runs the
real dashboard server on a free port against that folder.
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "agent"))
import actions  # noqa: E402
import dashboard  # noqa: E402

FIXTURE = ROOT / "agent" / "fixtures" / "queue.sample.json"
ROW_KEYS = {"invoice_id", "bucket", "amount", "account_name", "days_past_due", "state", "action_id"}


class QuietHandler(dashboard.Handler):
    def log_message(self, *args):
        pass


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-test-"))
        self.saved_env = os.environ.get("CHANNEL_ENV_FILE")
        os.environ["CHANNEL_ENV_FILE"] = str(self.tmp / "no-channel.env")  # never post to a real channel from tests
        self.saved_actions = (actions.OUT_DIR, actions.CRM_PATH)
        self.saved_dashboard = (dashboard.OUT_DIR, dashboard.CRM_PATH, dashboard.QUEUE_PATH)
        actions.configure_paths(self.tmp / "out", self.tmp / "crm.sqlite")
        with redirect_stdout(io.StringIO()):
            actions.main(["--dry-run", "--no-post", "--queue", str(FIXTURE), "--limit", "5"])
        dashboard.configure(self.tmp / "out", self.tmp / "crm.sqlite", FIXTURE)
        self.server = dashboard.Server(("127.0.0.1", 0), QuietHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        actions.configure_paths(*self.saved_actions)
        dashboard.configure(*self.saved_dashboard)
        if self.saved_env is None:
            os.environ.pop("CHANNEL_ENV_FILE", None)
        else:
            os.environ["CHANNEL_ENV_FILE"] = self.saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            exc.close()
            return exc.code, payload

    def status_file(self, invoice_id):
        return json.loads((self.tmp / "out" / "actions" / invoice_id / "status.json").read_text())

    def crm_state(self, invoice_id):
        with closing(sqlite3.connect(str(self.tmp / "crm.sqlite"))) as con:
            return con.execute("SELECT state FROM cases WHERE invoice_id = ? ORDER BY case_id DESC", (invoice_id,)).fetchone()[0]

    def test_summary(self):
        code, data = self.call("/api/summary")
        self.assertEqual(code, 200)
        self.assertEqual(data["totals"]["queued"], 6)
        self.assertEqual((data["snapshot_id"], data["as_of"]), ("2026-10-25T1430", "2026-10-25"))
        self.assertEqual(data["buckets"]["agent"], {"queued": 3, "dollars": 848.74, "not_drafted": 0, "drafted": 3,
                                                    "edited": 0, "approved": 0, "rejected": 0})
        self.assertEqual(data["buckets"]["writeoff"]["not_drafted"], 1)
        self.assertEqual((data["review"]["packages"], data["review"]["drafted"], data["review"]["approved"]), (5, 5, 0))
        self.assertEqual((data["last_run"]["model"], data["last_run"]["drafted"], data["runs"]), ("dry-run", 5, 1))

    def test_queue_rows_and_filters(self):
        code, data = self.call("/api/queue")
        self.assertEqual((code, data["total"]), (200, 6))
        self.assertEqual(set(data["rows"][0]), ROW_KEYS)
        first = data["rows"][0]
        self.assertEqual((first["invoice_id"], first["amount"], first["days_past_due"], first["state"]),
                         ("INV-104233", 18.4, 85, "drafted"))
        self.assertEqual(first["action_id"], "INV-104233:writeoff:2026-10-25T1430")
        _, data = self.call("/api/queue?bucket=writeoff")
        self.assertEqual([r["invoice_id"] for r in data["rows"]], ["INV-104233", "INV-118904"])
        _, data = self.call("/api/queue?state=not_drafted")
        self.assertEqual([(r["invoice_id"], r["action_id"]) for r in data["rows"]], [("INV-118904", None)])
        _, data = self.call("/api/queue?state=awaiting&limit=2")
        self.assertEqual((data["total"], data["returned"], len(data["rows"])), (5, 2, 2))
        self.assertEqual(self.call("/api/queue?bucket=nope")[0], 400)
        self.assertEqual(self.call("/api/queue?state=nope")[0], 400)
        self.assertEqual(self.call("/api/queue?limit=ten")[0], 400)

    def test_invoice(self):
        code, data = self.call("/api/invoice/INV-104233")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(data["artifacts"]), ["customer_email.md", "customer_slack.md", "internal_note.md",
                                                     "salesforce_case.json", "writeoff_memo.md"])
        self.assertTrue(data["artifacts"]["writeoff_memo.md"].strip().splitlines()[-1].startswith("Approver:"))
        self.assertEqual((data["status"]["state"], data["status"]["action_id"]), ("drafted", "INV-104233:writeoff:2026-10-25T1430"))
        self.assertEqual((data["item"]["invoice_id"], data["bucket"], data["days_past_due"]), ("INV-104233", "writeoff", 85))
        self.assertEqual(data["crm_case"]["state"], "drafted")
        _, data = self.call("/api/invoice/INV-100417")
        self.assertEqual(len(data["artifacts"]), 4)
        code, data = self.call("/api/invoice/INV-118904")
        self.assertEqual((code, data["status"], data["artifacts"]), (200, None, {}))
        self.assertEqual(self.call("/api/invoice/NOPE-1")[0], 404)
        self.assertEqual(self.call("/api/invoice/..%2F..%2Fetc")[0], 404)
        self.assertEqual(self.call("/api/nothing")[0], 404)

    def test_approve_reject_edit(self):
        code, data = self.call("/api/invoice/INV-104233/approve", {"by": "Mohammed"})
        self.assertEqual(code, 200)
        self.assertEqual((data["status"]["state"], data["status"]["by"]), ("approved", "Mohammed"))
        on_disk = self.status_file("INV-104233")
        self.assertEqual((on_disk["state"], on_disk["action_id"]), ("approved", "INV-104233:writeoff:2026-10-25T1430"))
        self.assertEqual(on_disk["drafted_at"], data["status"]["drafted_at"])
        self.assertEqual((self.crm_state("INV-104233"), data["crm_case_id"]), ("approved", 5))

        code, data = self.call("/api/invoice/INV-100417/reject", {"by": "Mohammed", "reason": "Dana's AP team changed, wrong contact"})
        self.assertEqual((code, data["status"]["state"], data["status"]["reason"]), (200, "rejected", "Dana's AP team changed, wrong contact"))
        self.assertEqual(self.crm_state("INV-100417"), "rejected")
        _, data = self.call("/api/invoice/INV-100417/approve", {})
        self.assertEqual((data["status"]["state"], data["status"]["by"]), ("approved", "openclaw"))
        self.assertNotIn("reason", self.status_file("INV-100417"))

        text = "Hi Hannah, invoice INV-102988 for $236.75 is 45 days past due. Could you send the remaining balance by November 2?"
        code, data = self.call("/api/invoice/INV-102988/edit", {"by": "Mohammed", "file": "customer_slack.md", "content": text})
        self.assertEqual((code, data["status"]["state"]), (200, "edited"))
        self.assertEqual((self.tmp / "out" / "actions" / "INV-102988" / "customer_slack.md").read_text(), text + "\n")
        self.assertEqual(self.crm_state("INV-102988"), "edited")

        case_path = self.tmp / "out" / "actions" / "INV-107752" / "salesforce_case.json"
        before = case_path.read_text()
        code, data = self.call("/api/invoice/INV-107752/edit", {"file": "salesforce_case.json", "content": "{not json"})
        self.assertEqual(code, 400)
        self.assertEqual((case_path.read_text(), self.status_file("INV-107752")["state"]), (before, "drafted"))

        self.assertEqual(self.call("/api/invoice/INV-107752/edit", {"file": "secrets.txt", "content": "x"})[0], 404)
        self.assertEqual(self.call("/api/invoice/INV-107752/edit", {"file": "customer_slack.md"})[0], 400)
        self.assertEqual(self.call("/api/invoice/INV-107752/approve", raw=b"{broken")[0], 400)
        self.assertEqual(self.call("/api/invoice/INV-118904/approve", {"by": "x"})[0], 404)
        self.assertEqual(self.call("/api/invoice/INV-107752/delete", {})[0], 404)

    def test_html_routes_unchanged(self):
        for path in ("/", "/invoice/INV-104233", "/cases", "/summary"):
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers["Content-Type"].startswith("text/html"))
        form = urllib.parse.urlencode({"by": "Mohammed"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/invoice/INV-109311/approve", data=form), timeout=10) as response:
            self.assertEqual(response.status, 200)  # 303 to the invoice page, followed
            self.assertIn("/invoice/INV-109311?done=approved", response.url)
        self.assertEqual((self.status_file("INV-109311")["state"], self.crm_state("INV-109311")), ("approved", "approved"))
        form = urllib.parse.urlencode({"by": "Mohammed", "file": "customer_slack.md", "content": "Hi Theo, invoice INV-109311 is past due."}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/invoice/INV-109311/save", data=form), timeout=10) as response:
            self.assertIn("done=edited", response.url)
        self.assertEqual(self.status_file("INV-109311")["state"], "edited")


if __name__ == "__main__":
    unittest.main()
