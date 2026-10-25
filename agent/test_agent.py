"""python -m pytest agent/   or   python agent/test_agent.py

Runs the drafting pipeline, approvals and the dashboard against a fake model server on 127.0.0.1, in a temp folder.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "channel"))
import actions  # noqa: E402
import store  # noqa: E402
import dashboard  # noqa: E402
import post  # noqa: E402

FIXTURE = HERE / "fixtures" / "queue.sample.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeModel(BaseHTTPRequestHandler):
    mode = "good"
    calls = 0

    def do_POST(self):
        FakeModel.calls += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        facts_msg = [m["content"] for m in body["messages"] if m["role"] == "user" and "invoice_id:" in m["content"]][-1]
        facts = dict(line.split(": ", 1) for line in facts_msg.splitlines() if ": " in line and not line.startswith("- "))
        iid, balance = facts["invoice_id"], facts["balance_due"]
        if FakeModel.mode == "garbage":
            content = "I am not able to help with that."
        elif "policy" in facts:
            content = json.dumps({
                "memo_rationale": f"The {balance} balance on {iid} is under the limit and long past due.",
                "internal_note": f"Hi, we propose writing off {iid}. Should we tell the customer?",
                "case_summary": f"Proposed write-off of {balance} on {iid}.",
                "next_action": "Controller reviews the memo.",
            })
        else:
            pay_by = facts["pay_by"]
            content = json.dumps({
                "email_subject": f"{iid}: {balance} past due",
                "email_body": f"Hello team,\n\nInvoice {iid} has {balance} open. Could you pay by {pay_by}?\n\nAccounts Receivable, OurCo",
                "slack_message": f"Hi team, {iid} has {balance} open. Could you confirm a date by {pay_by}?",
                "internal_note": f"Hi, {iid} is open. Is the billing contact still right?",
                "case_summary": f"{iid}, {balance} open.",
                "next_action": f"Follow up on {pay_by}.",
            })
        data = json.dumps({"choices": [{"message": {"content": "<think>x</think>" + content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@contextmanager
def sandbox(mode="good"):
    saved = {k: getattr(store, k) for k in ("OUT", "ACTIONS", "QUEUE", "SUMMARY", "CRM_DB")}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeModel.mode, FakeModel.calls = mode, 0
    os.environ["MODEL_BASE_URL"] = f"http://127.0.0.1:{server.server_port}/v1"
    os.environ["MODEL_NAME"] = "fake-model"
    with tempfile.TemporaryDirectory() as tmp:
        store.OUT = Path(tmp) / "out"
        store.ACTIONS = store.OUT / "actions"
        store.QUEUE = store.OUT / "queue.json"
        store.SUMMARY = store.OUT / "summary.json"
        store.CRM_DB = Path(tmp) / "crm.sqlite"
        try:
            yield Path(tmp)
        finally:
            server.shutdown()
            server.server_close()
            os.environ.pop("MODEL_BASE_URL")
            os.environ.pop("MODEL_NAME")
            for k, v in saved.items():
                setattr(store, k, v)


def run_actions(*args, queue=FIXTURE):
    sys.argv = ["actions.py", "--queue", str(queue), *args]
    return actions.main()


def cases():
    db = sqlite3.connect(store.CRM_DB)
    try:
        return {r[0]: r[1:] for r in db.execute("SELECT invoice_id, case_type, state, updated_by FROM cases")}
    finally:
        db.close()


def test_facts_are_computed_in_python_and_context_is_marked_internal():
    q = fixture()
    item = next(i for i in q["items"] if i["slack_thread"])
    text, pay_by = actions.facts(item, q)
    assert pay_by == date(2026, 11, 2)  # 2026-10-25 + 7 is a Sunday
    assert "pay_by: November 2, 2026" in text
    assert f"balance_due: ${item['balance']:,.2f}" in text
    assert "(internal, never quote to the customer)" in text


def test_checks_catch_missing_facts_threats_invented_amounts_and_leaks():
    q = fixture()
    item = q["items"][0]
    pay_by = date(2026, 11, 2)
    good = f"Invoice {item['invoice_id']} has ${item['balance']:,.2f} open, please pay by November 2, 2026."
    assert actions.problems(item, q, {"email_body": good}, pay_by) == {}
    assert "email_body" in actions.problems(item, q, {"email_body": f"{item['invoice_id']} is late."}, pay_by)
    assert "threats" in actions.problems(item, q, {"email_body": good + " We will start legal action."}, pay_by)["email_body"]
    assert "$999.99" in actions.problems(item, q, {"email_body": good + " Plus a $999.99 fee."}, pay_by)["email_body"]
    owner_first = item["account_owner"].split()[0]
    assert owner_first in actions.problems(item, q, {"slack_message": good + f" Per {owner_first}."}, pay_by)["slack_message"]
    assert actions.problems(item, q, {"internal_note": f"Hi {owner_first}, {item['invoice_id']} is open. Any news?"}, pay_by) == {}


def test_end_to_end_drafts_every_agent_and_writeoff_invoice_and_skips_disputes():
    with sandbox():
        assert run_actions() == 0
        q = fixture()
        for item in q["items"]:
            folder = store.ACTIONS / item["invoice_id"]
            if item["bucket"] == "escalate":
                assert not folder.exists()
                continue
            names = {p.name for p in folder.iterdir()}
            expected = {"customer_email.md", "customer_slack.md"} if item["bucket"] == "agent" else {"writeoff_memo.md"}
            assert expected | {"internal_note.md", "salesforce_case.json", "status.json", "queue_item.json"} == names
            assert store.read_status(item["invoice_id"])["state"] == "drafted"
            case = store.read_json(folder / "salesforce_case.json")
            assert case["template_fields"] == [] and case["model"] == "fake-model"
            assert "<think>" not in (folder / "internal_note.md").read_text(encoding="utf-8")
        email = (store.ACTIONS / "INV-116288" / "customer_email.md").read_text(encoding="utf-8")
        assert email.startswith("Subject: INV-116288") and "\n\n" in email
        assert len(cases()) == 5
        summary = store.read_json(store.SUMMARY)
        assert summary["drafted"] == 5 and summary["awaiting_approval"] == 5 and summary["last_run"]["drafted"] == 5


def test_bad_model_output_falls_back_to_templates_and_says_so():
    with sandbox("garbage"):
        assert run_actions("--limit", "1") == 0
        folder = next(store.ACTIONS.iterdir())
        case = store.read_json(folder / "salesforce_case.json")
        assert case["template_fields"]
        assert FakeModel.calls == 2  # first try plus one retry


def test_model_down_writes_nothing_and_fails():
    with sandbox():
        os.environ["MODEL_BASE_URL"] = "http://127.0.0.1:9/v1"
        assert run_actions("--limit", "2") == 1
        assert store.all_statuses() == {}


def test_same_snapshot_is_not_redrafted_and_new_snapshot_keeps_human_decisions():
    with sandbox() as tmp:
        run_actions()
        calls = FakeModel.calls
        assert run_actions() == 0 and FakeModel.calls == calls
        store.set_state("INV-116288", "approved", "Dana")
        store.set_state("INV-104225", "edited", "Dana", {"customer_slack.md": "Edited by hand INV-104225"})
        store.set_state("INV-116968", "rejected", "Dana")
        q = fixture()
        q["as_of"] = "2026-10-27"
        newer = tmp / "queue2.json"
        newer.write_text(json.dumps(q), encoding="utf-8")
        run_actions(queue=newer)
        assert store.read_status("INV-116288")["state"] == "approved"
        assert store.read_status("INV-104225")["state"] == "edited"
        assert "Edited by hand" in (store.ACTIONS / "INV-104225" / "customer_slack.md").read_text(encoding="utf-8")
        assert store.read_status("INV-116968")["action_id"].endswith(":2026-10-27")
        assert store.read_status("INV-113701")["action_id"].endswith(":2026-10-27")


def test_approval_updates_status_crm_memo_and_summary():
    with sandbox():
        run_actions()
        store.set_state("INV-100606", "approved", "Controller")
        memo = (store.ACTIONS / "INV-100606" / "writeoff_memo.md").read_text(encoding="utf-8")
        assert store.APPROVER_LINE not in memo and "Approved by Controller" in memo
        assert cases()["INV-100606"] == ("writeoff", "approved", "Controller")
        summary = store.build_summary(fixture())
        assert summary["written_off"] == 1 and summary["written_off_dollars"] == 0.03


def test_lock_stops_two_runs_drafting_the_same_invoice():
    with sandbox():
        assert store.claim("INV-1") and not store.claim("INV-1")
        store.release("INV-1")
        assert store.claim("INV-1")


def test_dashboard_pages_and_approve_over_http():
    with sandbox():
        run_actions()
        dashboard.QUEUE_PATH = FIXTURE
        dashboard._cache.update(mtime=None, queue=None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/", "/?tab=agent", "/?tab=writeoff", "/?tab=escalate&q=a", "/invoice/INV-116288",
                         "/invoice/INV-116288?edit=1", "/invoice/INV-100606", "/invoice/INV-113650", "/crm"):
                assert urllib.request.urlopen(base + path).status == 200, path
            for path in ("/invoice/NOPE", "/invoice/..%2F..%2Fetc", "/nothing"):
                try:
                    urllib.request.urlopen(base + path)
                    raise AssertionError(path)
                except urllib.error.HTTPError as e:
                    assert e.code == 404
            req = urllib.request.Request(base + "/invoice/INV-116288/approve", data=b"by=Dana", method="POST")
            urllib.request.urlopen(req)
            assert store.read_status("INV-116288")["by"] == "Dana"
            assert "Approved" in urllib.request.urlopen(base + "/invoice/INV-116288").read().decode()
        finally:
            server.shutdown()
            server.server_close()


def test_channel_post_fails_quietly_without_network():
    os.environ["SLACK_WEBHOOK_URL"] = "http://127.0.0.1:9/hook"
    try:
        assert post.send("hello") is False
    finally:
        os.environ.pop("SLACK_WEBHOOK_URL")


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
