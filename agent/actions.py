"""Draft the collections package for each queued invoice with the local model.

python agent/actions.py --limit 5
python agent/actions.py --invoice INV-116288
python agent/actions.py --dry-run --queue agent/fixtures/queue.sample.json --limit 6

Reads out/queue.json and platform/model.env. Writes out/actions/<invoice_id>/, the mock CRM in
agent/crm_mock/crm.sqlite and out/summary.json. The escalate bucket is left for a person.
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import store

PROMPTS = Path(__file__).resolve().parent / "prompts"
CASE_TYPE = {"agent": "collections_outreach", "writeoff": "writeoff"}
FIELDS = {
    "agent": ("email_subject", "email_body", "slack_message", "internal_note", "case_summary", "next_action"),
    "writeoff": ("memo_rationale", "internal_note", "case_summary", "next_action"),
}
THREATS = re.compile(r"\b(legal action|lawsuit|attorney|lawyer|court|sue|suing|collections? agency|credit bureau"
                     r"|credit report\w*|late fee|penalt\w*)\b", re.I)
CUSTOMER_FIELDS = ("email_subject", "email_body", "slack_message")
MONEY = re.compile(r"\$\s?([\d,]+\.\d{2})")
NO_FOLLOW_UP = re.compile(r"\b(no (prior|previous|earlier) (follow-?up|contact|outreach)|(nobody|no one) has (followed up|"
                          r"contacted)|never (been )?(followed up|touched|contacted)|not been followed up)", re.I)
WORKERS = 4
TIMEOUT = 300


def business_day(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def action_id(item, snapshot):
    return f"{item['invoice_id']}:{CASE_TYPE[item['bucket']]}:{snapshot}"


def facts(item, queue):
    as_of = date.fromisoformat(queue["as_of"])
    cur = item.get("currency", "USD")
    pay_by = business_day(as_of + timedelta(days=7))
    if item.get("last_touch_date"):
        touch = f"{store.long_date(item['last_touch_date'])} via {item['last_touch_source']} ({item['days_since_touch']} days ago)"
    else:
        touch = "never touched by anyone"
    lines = [
        f"invoice_id: {item['invoice_id']}",
        f"account: {item['account_name']} ({item['account_id']})",
        f"billing_contact: {item['billing_contact_email']}",
        f"account_owner: {item['account_owner']} <{item['owner_email']}>",
        f"invoice_amount: {store.money(item['amount'], cur)}",
        f"amount_paid: {store.money(item['amount_paid'], cur)}",
        f"balance_due: {store.money(item['balance'], cur)}",
        f"status: {item['status']}",
        f"issue_date: {store.long_date(item['issue_date'])}",
        f"due_date: {store.long_date(item['due_date'])}",
        f"days_past_due: {item['days_past_due']}",
        f"last_touch: {touch}",
    ]
    if item["bucket"] == "writeoff":
        p = queue["policy"]
        lines.append(f"policy: write_off_max_amount {store.money(p['write_off_max_amount'])}, "
                     f"min_days_past_due {p['min_days_past_due']}")
    else:
        lines.append(f"pay_by: {store.long_date(pay_by)}")
    notes = sorted(item.get("sheet_notes") or [], key=lambda n: n["date"])[-5:]
    if notes:
        lines.append("collector_notes (internal, never quote to the customer):")
        lines += [f"- {n['date']} {n['collector']}: {n['note']}" for n in notes]
    thread = item.get("slack_thread")
    if thread:
        lines.append(f"slack_thread {thread['channel']} (internal, never quote to the customer):")
        lines += [f"- {m['date']} {m['user']}: {m['text']}" for m in sorted(thread["messages"], key=lambda m: m["ts"])[-5:]]
    if item.get("conflict"):
        touches = ", ".join(f"{k} says {v}" for k, v in item["conflict"]["touches"].items())
        lines.append(f"source_conflict: {touches} ({item['conflict']['gap_days']} days apart)")
    return "\n".join(lines), pay_by


def load_prompt(bucket):
    parts = re.split(r"^## (input|output)\s*$", (PROMPTS / f"{bucket}.md").read_text(encoding="utf-8"), flags=re.M)
    messages = [{"role": "system", "content": parts[0].strip()}]
    for role, content in zip(parts[1::2], parts[2::2]):
        messages.append({"role": "user" if role == "input" else "assistant", "content": content.strip()})
    return messages


def chat(base_url, model, messages):
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 2000, "stream": False,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "low" if "gpt-oss" in model else "none"}
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        response = post_json(url, body)
    except urllib.error.HTTPError as e:
        if e.code not in (400, 422):
            raise
        del body["reasoning_effort"]
        response = post_json(url, body)
    content = response["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()


def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def parse(content, fields):
    match = re.search(r"\{.*\}", content, re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    return {f: str(data.get(f) or "").strip() for f in fields}


def problems(item, queue, draft, pay_by):
    iid = item["invoice_id"]
    balance = store.money(item["balance"], item.get("currency", "USD"))
    pay_by_text = f"{pay_by:%B} {pay_by.day}"
    policy = queue["policy"]
    known = {f"{x:,.2f}" for x in (item["amount"], item["amount_paid"], item["balance"],
                                   policy["write_off_max_amount"], policy["agent_max_amount"])}
    checks = {
        "email_subject": lambda t: iid in t and len(t) < 150,
        "email_body": lambda t: iid in t and balance in t and pay_by_text in t,
        "slack_message": lambda t: iid in t and pay_by_text in t and len(t) <= 600,
        "internal_note": lambda t: iid in t and "?" in t,
        "memo_rationale": lambda t: len(t) > 40,
        "case_summary": lambda t: 0 < len(t) <= 500,
        "next_action": lambda t: 0 < len(t) <= 300,
    }
    rules = {
        "email_subject": f"must name {iid}",
        "email_body": f"must state {iid}, the balance {balance} and the pay_by date {pay_by_text}",
        "slack_message": f"must name {iid} and the pay_by date {pay_by_text}, under 600 characters",
        "internal_note": f"must name {iid} and ask the owner one question",
        "memo_rationale": "two or three sentences",
        "case_summary": "one or two sentences",
        "next_action": "one sentence",
    }
    thread = item.get("slack_thread") or {}
    insiders = {item["account_owner"]} | {n["collector"] for n in item.get("sheet_notes") or [] if n.get("collector")}
    insiders |= {m["user"] for m in thread.get("messages", [])}
    insider_words = {w for name in insiders for w in name.split() if len(w) > 2}
    if thread:
        insider_words.add(thread["channel"])
    found = {}
    for field, text in draft.items():
        stray = set(MONEY.findall(text)) - known
        leaked = sorted(w for w in insider_words if re.search(r"(?<![\w#])" + re.escape(w) + r"(?!\w)", text))
        if not checks[field](text):
            found[field] = rules[field]
        elif field in CUSTOMER_FIELDS and leaked:
            found[field] = f"customer messages must not mention internal people or channels: {', '.join(leaked)}"
        elif THREATS.search(text):
            found[field] = "no threats, legal language or fees"
        elif stray:
            found[field] = f"uses amounts that are not in the facts: {', '.join('$' + s for s in sorted(stray))}"
        elif item.get("last_touch_date") and NO_FOLLOW_UP.search(text):
            found[field] = f"says nobody has followed up, but the last touch was {store.long_date(item['last_touch_date'])}"
    return found


def templates(item, queue, pay_by):
    """Fixed-shape text used for any field the model still gets wrong after one retry."""
    iid, account = item["invoice_id"], item["account_name"]
    owner = item["account_owner"].split()[0]
    balance = store.money(item["balance"], item.get("currency", "USD"))
    due, pay = store.long_date(item["due_date"]), store.long_date(pay_by)
    dpd, contact = item["days_past_due"], item["billing_contact_email"]
    if item["bucket"] == "writeoff":
        limit = store.money(queue["policy"]["write_off_max_amount"])
        return {
            "memo_rationale": f"The {balance} balance on {iid} is at or under the {limit} write-off limit and is {dpd} days "
                              f"past due. Collecting it would cost more in collector time than it recovers.",
            "internal_note": f"Hi {owner},\n\nWe are proposing to write off the {balance} balance on {iid} for {account} "
                             f"under the small-balance policy. It is {dpd} days past due.\n\nDo you want the customer told "
                             f"about the write-off, or should we close it without contacting them?\n\nAccounts Receivable",
            "case_summary": f"Proposed write-off of {balance} on {iid} under the {limit} small-balance limit; {dpd} days past due.",
            "next_action": "Controller reviews and signs the write-off memo.",
        }
    return {
        "email_subject": f"{iid}: {balance} past due since {due}",
        "email_body": f"Hello {account} accounts payable team,\n\nOur records show invoice {iid} has a balance of {balance}. "
                      f"It was due {due} and is now {dpd} days past due.\n\nCould you send payment, or reply with the date "
                      f"it is scheduled, by {pay}? If it has already been paid, a copy of the remittance will let us close "
                      f"it on our side.\n\nThank you,\nAccounts Receivable, OurCo",
        "slack_message": f"Hi team, invoice {iid} has {balance} open since {due}. Could you confirm a payment date by {pay}? "
                         f"If it is already paid, a remittance copy lets us close it out.",
        "internal_note": f"Hi {owner},\n\n{iid} for {account} has {balance} open and is {dpd} days past due. We are sending "
                         f"{contact} a reminder asking for payment or a payment date by {pay}.\n\nIs {contact} still the "
                         f"right billing contact for {account}?\n\nAccounts Receivable",
        "case_summary": f"{iid}, {balance}, {dpd} days past due. Payment reminder drafted to {contact}.",
        "next_action": f"Send the approved reminder and check for payment on {pay}.",
    }


def draft(item, queue, base_url, model):
    start = time.perf_counter()
    fields = FIELDS[item["bucket"]]
    fact_text, pay_by = facts(item, queue)
    prompt = load_prompt(item["bucket"])
    result = parse(chat(base_url, model, prompt + [{"role": "user", "content": fact_text}]), fields)
    bad = problems(item, queue, result, pay_by)
    if bad:
        fixes = "\n".join(f"- {k}: {v}" for k, v in bad.items())
        retry_text = f"{fact_text}\n\nA previous draft for this invoice had these problems. Avoid them:\n{fixes}"
        retry = parse(chat(base_url, model, prompt + [{"role": "user", "content": retry_text}]), fields)
        still_bad = problems(item, queue, retry, pay_by)
        for field in bad:
            if field not in still_bad:
                result[field] = retry[field]
        bad = {f: v for f, v in bad.items() if f in still_bad}
    fallback = templates(item, queue, pay_by)
    for field in bad:
        result[field] = fallback[field]
    return result, sorted(bad), pay_by, time.perf_counter() - start


def writeoff_memo(item, queue, rationale):
    p = queue["policy"]
    cur = item.get("currency", "USD")
    if item.get("last_touch_date"):
        touch = f"{store.long_date(item['last_touch_date'])} via {item['last_touch_source']}"
    else:
        touch = "never touched"
    return f"""# Write-off memo: {item['invoice_id']}

Account: {item['account_name']} ({item['account_id']})
Account owner: {item['account_owner']}
Invoice amount: {store.money(item['amount'], cur)}, paid {store.money(item['amount_paid'], cur)}, balance {store.money(item['balance'], cur)}
Issued {store.long_date(item['issue_date'])}, due {store.long_date(item['due_date'])}, {item['days_past_due']} days past due as of {store.long_date(queue['as_of'])}
Status: {item['status']}. Last touch: {touch}.

Policy: policy.yaml write_off_max_amount = {p['write_off_max_amount']:.2f}. Balances at or under this amount that are at least {p['min_days_past_due']} days past due are written off rather than worked.

Rationale: {rationale}

Recommendation: write off {store.money(item['balance'], cur)} to small-balance write-offs and close the invoice.

{store.APPROVER_LINE}
"""


def write_package(item, queue, result, template_fields, pay_by, model, aid):
    folder = store.action_dir(item["invoice_id"])
    folder.mkdir(parents=True, exist_ok=True)
    for name in store.EDITABLE + ("posted.json",):
        (folder / name).unlink(missing_ok=True)
    if item["bucket"] == "agent":
        store.write_text(folder / "customer_email.md", f"Subject: {result['email_subject']}\n\n{result['email_body']}\n")
        store.write_text(folder / "customer_slack.md", result["slack_message"] + "\n")
        next_date = pay_by
    else:
        store.write_text(folder / "writeoff_memo.md", writeoff_memo(item, queue, result["memo_rationale"]))
        next_date = business_day(date.fromisoformat(queue["as_of"]) + timedelta(days=3))
    store.write_text(folder / "internal_note.md", result["internal_note"] + "\n")
    case = {
        "invoice_id": item["invoice_id"],
        "action_id": aid,
        "case_type": CASE_TYPE[item["bucket"]],
        "summary": result["case_summary"],
        "next_action": result["next_action"],
        "next_action_date": next_date.isoformat(),
        "drafted_at": store.now(),
        "model": model,
        "template_fields": template_fields,
    }
    case["case_number"] = f"CASE-{store.crm_record(item, case):06d}"
    store.write_json(folder / "queue_item.json", item)
    store.write_json(folder / "salesforce_case.json", case)
    store.write_json(folder / "status.json", {"state": "drafted", "by": "agent", "at": case["drafted_at"], "action_id": aid})


def needs_draft(item, snapshot):
    status = store.read_status(item["invoice_id"])
    if status is None:
        return True
    return status["state"] not in store.HUMAN_STATES and status.get("action_id") != action_id(item, snapshot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, default=store.QUEUE)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--bucket", choices=sorted(CASE_TYPE))
    ap.add_argument("--invoice", action="append", help="draft this invoice; repeat for several")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    started_at, start = store.now(), time.perf_counter()
    queue = store.load_queue(args.queue)
    snapshot = store.snapshot_id(queue)
    buckets = [args.bucket] if args.bucket else list(CASE_TYPE)
    items = [i for i in queue["items"] if i["bucket"] in buckets and (not args.invoice or i["invoice_id"] in args.invoice)]
    if args.invoice and not items:
        raise SystemExit(f"{', '.join(args.invoice)} not in the agent or writeoff bucket of {args.queue}")
    pending = [i for i in items if needs_draft(i, snapshot)]
    pending.sort(key=lambda i: store.read_status(i["invoice_id"]) is not None)
    todo = pending[:args.limit] if args.limit else pending
    base_url, model = store.model_config()

    if args.dry_run:
        print(f"dry run: {len(pending):,} of {len(items):,} invoices need drafts, would draft {len(todo)} with {model} at {base_url}")
        for item in todo:
            print(f"  {item['invoice_id']}  {item['bucket']:<8}  {store.money(item['balance']):>9}  "
                  f"{item['days_past_due']} days past due  -> {action_id(item, snapshot)}")
        if todo:
            print("\nfacts sent to the model for the first one:\n" + facts(todo[0], queue)[0])
        return 0

    todo = [i for i in todo if store.claim(i["invoice_id"])]
    drafted, failed, template_count = 0, 0, 0
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            futures = {pool.submit(draft, item, queue, base_url, model): item for item in todo}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result, template_fields, pay_by, seconds = future.result()
                except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
                    print(f"{item['invoice_id']}: model call failed: {e}")
                    failed += 1
                    continue
                write_package(item, queue, result, template_fields, pay_by, model, action_id(item, snapshot))
                drafted += 1
                template_count += len(template_fields)
                note = f", template for {', '.join(template_fields)}" if template_fields else ""
                print(f"drafted {item['invoice_id']} ({item['bucket']}, {store.money(item['balance'])}) in {seconds:.1f}s{note}")
    finally:
        for item in todo:
            store.release(item["invoice_id"])

    duration = round(time.perf_counter() - start, 1)
    run = {"started_at": started_at, "duration_s": duration, "drafted": drafted, "failed": failed,
           "template_fields": template_count, "pending": len(pending) - drafted, "model": model, "snapshot_id": snapshot}
    store.build_summary(queue, run)
    print(f"drafted {drafted}, failed {failed}, {template_count} fields fell back to templates, "
          f"{len(pending) - drafted:,} still to draft; model {model}; {duration}s")
    return 1 if failed and not drafted else 0


if __name__ == "__main__":
    raise SystemExit(main())
