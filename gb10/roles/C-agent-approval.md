# Role C, Agent actions and approval

Goal: for each queued invoice, five drafted artifacts from the local model, a mock Salesforce record, and a one-click approval surface. Reads `out/queue.json`, reads `platform/model.env`, writes `out/actions/`.

## `agent/actions.py`
- Args: `--limit N`, `--bucket`, `--dry-run`. Skips invoices already in `out/actions/` with a status other than rejected.
- Calls `MODEL_BASE_URL/chat/completions` with `MODEL_NAME` (OpenAI-compatible JSON over `urllib`, no SDK). Temperature low. One call per artifact, or one call returning a JSON object with all four texts if the model handles it; test both on qwen3:8b and keep the one that is reliable.
- Prompts in `agent/prompts/*.md`, templated: the model fills facts into a fixed shape. Include two few-shot examples per artifact. Tone: direct, courteous, names the invoice number, amount, due date, days past due, what we need, by when. Never threatens. Internal note names the account owner and asks one specific question.
- Write-off memo for the writeoff bucket cites the policy clause from `policy.yaml`, amount, age, and leaves an approver line.
- Writes `salesforce_case.json` and inserts a row into `agent/crm_mock/crm.sqlite` (tables: invoices, cases).
- Appends to `out/summary.json`.

## `agent/skills/collections/SKILL.md`
The OpenClaw skill wrapper so the agent can be asked in chat: "work the queue", "show me INV-0042", "approve INV-0042". Thin: it shells out to the scripts above.

## `channel/dashboard.py`
Single-file web app (stdlib `http.server` or Flask if installed) on port 8090: queue table by bucket, click an invoice to see the five artifacts side by side, Approve / Edit / Reject buttons that update `status.json` and the CRM case. This is the demo fallback and the thing judges click. Make it look like a tool a collector would open.

## `channel/post.py`
Posts one package per invoice to Slack (incoming webhook or bot token) or Telegram (bot API), with approve and reject buttons where the platform allows, otherwise a reply command. If the network call fails, log and continue. The dashboard must never depend on this.

## `agent/summary.py`
Reads `out/summary.json`, posts the heartbeat line: queued, drafted, approved, written off, dollars, model, last run time.

## Smoke test
`python agent/actions.py --limit 3` against qwen3:8b produces three folders with readable drafts in under 60 seconds. Then swap `MODEL_NAME` to the big model and check quality.
