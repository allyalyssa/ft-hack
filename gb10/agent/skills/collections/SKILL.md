---
name: collections
description: Reviews the small-dollar collections queue through the approval dashboard on the host. Lists what is waiting for review, shows one invoice's drafted package, records a collector's approve, reject (with the reason) or edit, and reports how collections is doing today. Use when someone says "what's in the queue", "show me INV-0042", "approve INV-0042", "reject INV-0042 because ...", or "how is collections doing today".
---

# Collections

Read-only for you. Approving or rejecting is the collector's decision: when someone asks you to approve or reject an invoice, tell them to type `approve INV-…` or `reject INV-… because …` in the desk's chat panel, or press the button on the invoice page. Do not call the approve, reject or edit endpoints yourself.

For "biggest" questions add `&sort=amount` (largest first) to the queue call; for "oldest" add `&sort=days_past_due`.

The pipeline runs on the host. Cron runs triage and drafting every 2 minutes, and the dashboard serves the results on port 8090. You run inside a sandbox that cannot see the host's files, so every step below is one curl call to the dashboard's JSON API. Never read or write files for this skill, and never try to run the pipeline scripts.

Set the address once per session and check it answers:

```bash
DASHBOARD_URL="${DASHBOARD_URL:-http://host.openshell.internal:8091}"
curl -sS --max-time 10 "$DASHBOARD_URL/api/summary" > /dev/null && echo "dashboard reachable at $DASHBOARD_URL"
```

Inside the OpenShell sandbox `host.openshell.internal` resolves to the host (172.18.0.1 on the GB10), and the sandbox policy rule `collections_dashboard` allows curl to reach port 8090. If the check fails, try `DASHBOARD_URL=http://host.openshell.internal:8090`, then `http://172.18.0.1:8091`. If it still fails, tell the person the dashboard is unreachable from the sandbox and stop.

Every reply is JSON with an `"ok"` field. When it is false, tell the person the `"error"` text. Do not repeat a POST more than once. Add `?pretty=1` to a GET when you want indented JSON.

Nothing reaches a customer from here. Approve or reject only when the person asks for that invoice by id. Never decide on your own, and never approve more invoices than they named.

## "what's in the queue"

```bash
curl -sS --max-time 10 "$DASHBOARD_URL/api/queue?state=awaiting&sort=amount&limit=20"
```

Reply with `total` (packages waiting for review), then one line per row: `invoice_id`, `account_name`, `amount`, `days_past_due` days past due, `bucket`. For one bucket add `&bucket=agent`, `&bucket=writeoff` or `&bucket=escalate`. Other states: `drafted`, `edited`, `approved`, `rejected`, `not_drafted`.

## "show me INV-0042"

```bash
curl -sS --max-time 10 "$DASHBOARD_URL/api/invoice/INV-0042"
```

Start with `status.state`, plus `status.by` and `status.reason` when present. Then quote each draft in `artifacts` exactly, under its own heading: `customer_email.md` (the first line is the subject), `customer_slack.md`, `internal_note.md`, the `summary` and `next_action` from `salesforce_case.json`, and `writeoff_memo.md` when there is one. If `status` is null, the agent has not drafted it yet and a later cron run will. A 404 means the invoice id is unknown.

## "approve INV-0042"

```bash
curl -sS --max-time 10 -X POST "$DASHBOARD_URL/api/invoice/INV-0042/approve" \
  -H 'Content-Type: application/json' --data-binary @- <<'JSON'
{"by": "Mohammed"}
JSON
```

Put the person's name in `by`. Confirm with `status.state`, `status.by`, `status.at` and `crm_case_id` from the reply. For "approve all" or a list, first run the queue call above, show the invoices, and wait for a yes. Then approve each one with its own call.

## "reject INV-0042 because ..."

```bash
curl -sS --max-time 10 -X POST "$DASHBOARD_URL/api/invoice/INV-0042/reject" \
  -H 'Content-Type: application/json' --data-binary @- <<'JSON'
{"by": "Mohammed", "reason": "Wrong contact, Dana moved to another team in September"}
JSON
```

Use the person's own words for `reason`. Inside the JSON, write a double quote as `\"`. Confirm with `status.state` and `status.reason`. The agent drafts a rejected package again on its next run.

## "change the Slack message on INV-0042 to ..."

```bash
curl -sS --max-time 10 -X POST "$DASHBOARD_URL/api/invoice/INV-0042/edit" \
  -H 'Content-Type: application/json' --data-binary @- <<'JSON'
{"by": "Mohammed", "file": "customer_slack.md", "content": "Hi Dana, invoice INV-0042 for $480.00 is 62 days past due. Could you send payment or a pay date by November 2?"}
JSON
```

`file` is one of `customer_email.md` (subject line, blank line, body), `customer_slack.md` (under 600 characters), `internal_note.md`, `writeoff_memo.md` or `salesforce_case.json` (valid JSON). Write line breaks as `\n`. An edit sets the state to edited; it still needs an approve.

## "how is collections doing today"

```bash
curl -sS --max-time 10 "$DASHBOARD_URL/api/summary"
```

Reply in a few lines:
- queued invoices and dollars: `totals.queued`, `totals.dollars_queued` (called `totals.queued_dollars` in some builds), and `buckets.<bucket>.queued`
- waiting for review: `review.awaiting_review`, and per bucket `buckets.<bucket>.drafted` (a draft waiting for a person). `not_drafted` means the agent has not reached that invoice yet. Never say a bucket has no drafts when its `drafted` count is above zero.
- approved: `review.approved` and `review.approved_dollars`
- written off: `review.written_off_dollars`
- rejected: `review.rejected`
- last agent run: `last_run.run_at`, `last_run.model`, `last_run.drafted`

Keep it to six lines or fewer. Use plain numbers with commas and dollars to the cent.
