> Historical implementation reference: preserved from the previous root README. For the current integrated service layout and model configuration, start with [the project README](../README.md). Commands below run from the repository root.

# Collex

A collections agent that runs offline on an NVIDIA GB10. It finds the small invoices that collectors have lost track
of, drafts the follow-ups and proposes write-offs, all for a person to approve.

## Run it

```
ollama pull qwen3:8b               # once, on a machine with internet; see platform/RUNBOOK.md for the offline box
python platform/preflight.py       # tests, local model, one full cycle, dashboard; all under the offline guard
python platform/run.py             # dashboard on http://localhost:8080 plus a cycle every 2 minutes
```

### The collector workspace

```
python app/server.py               # http://<box>:8000 ; approvals are forwarded to channel/dashboard.py on :8080
```

The invoice board covers the complete export, with search across invoice IDs, accounts and owners, status and owner
filters, sortable columns, and 50 invoices per page. Portfolio totals reflect the full snapshot. On phones, invoice
identity and balance stay visible together; remaining columns scroll horizontally.

Approvals, Accounts (invoices grouped by customer), PO cases, Disputes and Write-offs retain their review workflows.
**Ask Collex** uses the plush companion as its face and opens a persistent chat sidebar beside the ledger on desktop.
On smaller screens it opens as a collapsible panel. Escape or the close button collapses chat.
Ask "Has INV-112347 been approved?", "Why hasn't INV-100036 been drafted?", "Which PO cases are ready?" or an account
name; every answer comes from lookups on the agent's own files and lists them under "How I found this". Approve,
reject and "draft it now" go through the approval dashboard, which owns those decisions.

`platform/RUNBOOK.md` covers the GB10: copying models to a box with no internet, always-on services, the live drop
and pulling the cable. Drafting and approval are in `agent/README.md`.

## Role B: data and triage

No model is used anywhere in this part. Python 3.10+ and the standard library only (PyYAML is used if installed).

```
python data/generate.py            # 20,000-invoice fake world -> data/exports/
python triage/triage.py            # -> out/queue.json and out/headline.json
python -m pytest triage/           # or: python triage/test_triage.py
```

### Live drop

```
python data/generate.py --delta    # 200 invoices, two days later -> data/delta/
cp data/delta/* data/inbox/
python triage/triage.py            # queue goes from 5,332 to 5,688
```

Triage reads `data/inbox/` when it holds a `salesforce_invoices.csv`, otherwise `data/exports/`. A bundle whose
`manifest.json` says `"kind": "delta"` is laid over `data/exports/` instead of replacing it. Empty the inbox afterwards.

### Inputs (one export bundle)

| File | What it is |
|---|---|
| `salesforce_invoices.csv` | One row per invoice: account, owner, amount, amount_paid, status, due date, last touch, dispute flag |
| `collector_sheet.csv` | Collector notes, one row per touch: invoice_id, collector, touch_date, note |
| `slack_threads.json` | Slack threads about an invoice, with last_message_date |
| `manifest.json` | `kind` (full or delta) and `as_of`, the snapshot date that all ageing is measured from |

### Policy (`policy.yaml`)

An invoice is queued when it is not paid, at least `min_days_past_due` past due, untouched for at least
`stale_touch_days` (or never touched), and its remaining balance is at most `agent_max_amount`.
Buckets: `writeoff` if the balance is at most `write_off_max_amount`, `escalate` if disputed, otherwise `agent`.

The latest touch across Salesforce, the collector sheet and Slack wins. When sources disagree by `stale_touch_days`
or more, the invoice is listed under `conflicts`.

### Outputs

- `out/queue.json`: `as_of`, `policy`, `totals`, `items` (oldest first, with sheet notes and the Slack thread so the
  drafting step has context) and `conflicts`.
- `out/headline.json`: the slide numbers, including hours at `minutes_per_touch` per queued invoice.

### Whole-account context

Before anyone chases a $40 invoice, triage looks at the whole account. Every queued item carries
`account_context` (open invoices and balance on the account, the largest other invoice, open disputes, the last touch
anywhere on the account) and a `package_id`; `queue.json` lists `packages`, the queued invoices grouped by account
(5,332 invoices become 1,071 packages). `coordinate` is set when a person touched an open invoice over $100,000 on
the same account in the last `stale_touch_days`: fold the small one into that conversation instead of sending a
separate reminder (1,814 invoices).

Write-offs carry `writeoff_controls`: what the whole account would write off, the `account_writeoff_max` limit
(default $25), a batch of 5 or more on one account, and the `policy_version` the decision used. `tiny_origin`
separates leftovers after partial payments from invoices issued for cents; `billing_leads` lists accounts with 3+ tiny
balances as questions for billing operations, not proven errors.

### Stale drafts

`python triage/revalidate.py` re-checks every drafted, edited or approved action against the newest export and
withdraws it when the invoice was paid, its balance changed, the customer disputed it, the billing contact changed, a
person touched it after the draft, or it left the queue. It writes `out/actions/<id>/revalidation.json` beside each
draft and `out/revalidation.json`; it never edits the draft itself. The live drop (`generate.py --delta`) changes the
first five invoices the agent drafts, so the demo shows withdrawals with their reasons.

## Disputes (the escalate bucket)

The drafting agent leaves disputed invoices for a person. This step makes sure that person gets a worked case
instead of a bare flag. No model; it runs right after triage.

```
python disputes/disputes.py        # -> out/disputes/
python -m pytest disputes/         # or: python disputes/test_disputes.py
```

```
python disputes/board.py           # dispute desk, read-only, http://<box>:8090
```

## The whole cycle

`scripts/cycle.sh` runs one cycle: triage, disputes, drafting (`LIMIT`, default 5), posting and the summary. It logs
each step with its time, stops on the first failure or after `STEP_TIMEOUT` seconds (default 900), and skips a run
while the previous one is still going. On the box:

```
*/2 * * * * cd /path/to/ft-hack && mkdir -p out && sh scripts/cycle.sh >> out/cycle.log 2>&1
```

Cron starts with a bare environment, so put `PYTHON=/usr/bin/python3` (and any `MODEL_*` overrides) at the top of
the crontab if the defaults are wrong.

For each disputed invoice in `out/queue.json`:

- **Reason**, read from the collector-sheet notes and Slack messages triage attached: already paid, usage overage,
  missing PO or vendor portal, invoice paperwork, payment since promised (a stale dispute flag), or no reason recorded.
  The matching lines are kept as evidence.
- **Owner**: deal desk for usage overages, billing ops for "already paid" and paperwork, otherwise the account owner.
- **Priority and deadline**: P1 if the balance is $250+ or 90+ days past due, P2 at 60+ days, else P3; respond within
  2, 5 or 10 business days of the snapshot date.
- **Recommendation**: the playbook step for the reason, or a credit memo when the balance is at or under
  `--concede-max` (default $25), except when the customer says it was paid or has promised to pay.
- **Drafts**: a Salesforce dispute case, a note to the owning team (cc the account owner), and a holding reply to the
  customer that never asks for payment.

Outputs: `out/disputes/<invoice_id>.json`, `out/disputes/salesforce_cases.csv` (one row per case, Data Loader
columns) and `out/disputes/summary.json` (counts and dollars by reason, owner and priority, for the deck).

### Payment blockers (missing PO)

A missing-PO dispute becomes a case the agent carries until there is evidence: asked the owner, owner accepted, PO
received, PO checked against the invoice (same account, covers the balance, not expired, lists this invoice). A
matching PO gives a corrected package (reissued invoice email, Salesforce update); a contradicting one keeps the case
open with each mismatch. Cases live in `out/disputes/cases/` across runs and close only when the invoice leaves the
dispute queue. Owner replies are read from `data/responses/*.json`; for the offline demo,
`python disputes/make_responses.py` writes clearly labelled synthetic replies (one matching PO, one for the wrong
account, one accept with no PO yet). The dispute desk shows each case's timeline; open one directly with
`http://<box>:8090/#INV-118955`.

All names are invented and every email domain uses the reserved `.example` TLD.
