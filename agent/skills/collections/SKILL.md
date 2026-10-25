---
name: collections
description: Work the small-dollar collections queue. Triage the latest export, draft follow-ups and write-off memos with the local model, show a drafted package, and approve or reject it. Use when asked to "work the queue", "show me INV-...", "approve INV-...", "reject INV-...", or for the collections status.
---

# Collections queue

All commands run from the repo root, the folder that holds `triage/`, `agent/` and `channel/`.
They only read and write files on this machine. Nothing here sends anything to a customer.

| Request | Command |
|---|---|
| Work the queue | `python3 triage/triage.py && python3 agent/actions.py --limit 5` |
| Draft one invoice | `python3 agent/actions.py --invoice INV-116288` |
| What is waiting for approval | `python3 agent/status.py list` |
| Show me INV-116288 | `python3 agent/status.py show INV-116288` |
| Approve INV-116288 | `python3 agent/status.py approve INV-116288 --by "<name of the person asking>"` |
| Reject INV-116288 | `python3 agent/status.py reject INV-116288 --by "<name of the person asking>"` |
| Status / summary | `python3 agent/summary.py --no-post` |

Rules:
- Approve or reject only when the person names the invoice and says approve or reject. Never approve in bulk on your own initiative.
- When showing a package, paste the drafts as they are. Do not rewrite them in chat; edits go through the dashboard so they are saved.
- Invoices in the escalate bucket are disputed and are left to a person. Say so instead of drafting them.
- The dashboard is at port 8080 on this machine for anything that needs a click.
