# Column ablation, simulated collectors

Model qwen3:8b; 40 queued agent-bucket invoices sampled evenly across expected actions; 4 personas x
5 views x 2 reps; 1600 decisions, 5 invalid or missing answers (scored wrong);
rescored from raw.jsonl.

Expected action: the newest note matching a blocker phrase decides (a promise counts only if under 30
days old); otherwise bundle when the account has 3+ queued invoices, else a standard reminder. Harmful touch: a
standard or bundled reminder sent to a customer who says they paid or recently promised a date. Views that show notes
contain part of the answer, so their gains are partly by construction. Synthetic agents are not human evidence.

## By view

| view | n | correct (95% CI) | harmful touch (95% CI) | bundled when it should (95% CI) |
|---|---|---|---|---|
| current | 320 | 12% (9%-17%) | 100% (95%-100%) | 0% (0%-9%) |
| exposure | 320 | 15% (12%-20%) | 96% (90%-99%) | 20% (10%-35%) |
| note | 320 | 75% (70%-79%) | 2% (1%-9%) | 0% (0%-9%) |
| blocker | 320 | 78% (73%-82%) | 1% (0%-7%) | 0% (0%-9%) |
| all_three | 320 | 79% (74%-83%) | 2% (1%-9%) | 8% (3%-20%) |

## By persona (all views)

| persona | n | correct (95% CI) | harmful touch (95% CI) | bundled when it should (95% CI) |
|---|---|---|---|---|
| a collector in your first week | 400 | 52% (47%-57%) | 40% (31%-50%) | 2% (0%-10%) |
| a relationship-minded collector who avoids annoying good customers | 400 | 52% (47%-57%) | 40% (31%-50%) | 8% (3%-19%) |
| a veteran collector with ten years in accounts receivable | 400 | 52% (47%-57%) | 41% (32%-51%) | 4% (1%-13%) |
| an overloaded collector covering 400 accounts who has to move fast | 400 | 52% (47%-57%) | 41% (32%-51%) | 8% (3%-19%) |

## Current view: expected -> chosen (counts)

| expected | chosen | n |
|---|---|---|
| ask_remittance | send_reminder | 40 |
| bundle_account | send_reminder | 38 |
| bundle_account | ask_remittance | 1 |
| bundle_account | route_to_owner | 1 |
| find_contact | send_reminder | 40 |
| fix_po | send_reminder | 40 |
| resend_invoice | send_reminder | 40 |
| route_to_owner | send_reminder | 40 |
| send_reminder | send_reminder | 40 |
| wait_for_promise | send_reminder | 40 |
