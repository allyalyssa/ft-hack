# Collex: 1 minute 55 second demo

Use a 23-second opening animation with narration, followed by product footage. Four consecutive speakers; no spoken handoff phrases. Do not repeat the opening animation.

| Speaker | Time | Narration | Picture |
|---|---|---|---|
| 1 | 0:00–0:10 | A forty-dollar invoice can require the same investigation as a forty-thousand-dollar invoice. Collections teams naturally prioritize the larger balance. | Animation: large invoices receive attention; small ones accumulate. |
| 1 | 0:10–0:23 | But small invoices accumulate. Distributed collectors and onsite finance teams piece together emails, notes, and account history across time zones. The coordination consumes time. | Animation: fragmented context and handoffs; transition to Collex. |
| 1 | 0:23–0:30 | Meet Collex: a local agent for the small-balance backlog, with collectors in control. | Workspace, then Upload export. |
| 2 | 0:30–0:40 | First, we upload twenty thousand invoices. Collex runs the collection rules and shows exactly which invoices qualify, and why. | First bundle: select, submit, actual received count and filters. |
| 2 | 0:40–0:50 | Now a second twenty-thousand-invoice export goes through the same workflow: overdue, untouched balances below the collector threshold. | Second bundle and report; show actual active queue count. |
| 2 | 0:50–0:56 | Whole-account context flags small invoices that need coordination with an existing account owner. | Account and coordination flag. |
| 3 | 0:56–1:10 | Here, payment is blocked by a missing purchase order. Collex tracks the owner's response, checks the PO against the invoice, and prepares the corrected follow-up. | PO case before/after a labeled synthetic owner reply. |
| 3 | 1:10–1:22 | The local model drafts the customer message, internal note, and case record. Collectors review, edit, approve, or reject. Small write-offs require policy approval too. | Evidence, draft, edit, approval, saved local record, write-off memo. |
| 4 | 1:22–1:34 | Approval isn't permanent permission. When a new export shows a payment, dispute, or changed contact, Collex withdraws the outdated draft and records why. | Changed export, same invoice's withdrawal reason. |
| 4 | 1:34–1:43 | Inference and case processing run on the GB10. With the network disconnected, the local approval workflow continues. | Actual GB10 model evidence and dashboard with uplink disconnected. |
| 4 | 1:43–1:53 | FinEng can build this, but larger projects come first. Collex gives finance teams a dedicated small-balance workflow without adding another internal engineering project. | Completed package and buyer-value overlay. |
| — | 1:53–1:55 | No speech. | Collex — Local collections. Human control. |

## Recording preparation

- Record the deployed implementation and actual configured model. Capture disconnected GB10 proof on its own display; a remote connection can vanish when unplugged. Identify laptop footage as a local laptop demo.
- Keep 'Synthetic demo data' visible. Responses from `python disputes/make_responses.py` must be labeled simulated.
- Select only supported export files, one bundle per submission. Full manifests replace earlier uploads; matching IDs update records. Do not imply 40,000 unique active invoices without checking reports.
- If the second upload updates the same invoices, say: 'Now we upload an updated twenty-thousand-invoice snapshot and rerun the rules against the latest payment and contact information.'
- Capture actual uploads and results. Trim waiting with 'Elapsed time omitted'; label sped-up footage. Import runs rules, revalidation and dispute routing, not model drafting for every invoice.
- Leave three-second editing handles around shots. Prepare a few genuine model drafts before recording.
- At 1:22 use an additional prepared update if the second export did not change the demonstrated invoice. A seeded payment is not agent-generated cash recovery.
- Only narrate features present in the recorded deployment. Substitute evidence/review footage and matching narration if a feature is unavailable.

## Submission assets

This repository contains the plan, not a published video. Add the final video URL and a real workspace screenshot to the README once available. Do not substitute an unrelated demo or fabricated UI image.
