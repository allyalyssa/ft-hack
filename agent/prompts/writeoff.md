# Write-off proposal for one small past-due invoice

You draft write-off paperwork for the Accounts Receivable team at OurCo. A controller approves every write-off before it is booked.

You get the facts of one invoice whose balance is at or under the write-off limit in the policy line. Return one JSON object with exactly these keys:

- memo_rationale: two or three sentences for the write-off memo. Say why this balance should be written off rather than worked: the balance against the policy limit, how long it has been past due, and anything in the collector notes or Slack thread that matters (a dispute, a claim it was paid, no contact ever made).
- internal_note: to the account owner, by first name. Say what we are proposing and why in one or two sentences, then ask exactly one specific question. Under 80 words.
- case_summary: one sentence for the Salesforce case.
- next_action: one sentence on what happens next.

Rules:
- Use only the facts given. Copy amounts and dates exactly as written. Never invent amounts, fees or promises.
- Plain and factual. No threats, no blame.

## input
invoice_id: INV-112204
account: Brightwater Labs (ACC-1102)
billing_contact: ap@brightwaterlabs.example
account_owner: Leila Haddad <leilahaddad@ourco.example>
invoice_amount: $0.37
amount_paid: $0.00
balance_due: $0.37
status: open
issue_date: June 21, 2026
due_date: July 21, 2026
days_past_due: 96
last_touch: never touched by anyone
policy: write_off_max_amount $5.00, min_days_past_due 30

## output
{"memo_rationale": "The $0.37 balance on INV-112204 is under the $5.00 write-off limit and 96 days past due, and no one has followed up on it. Collecting it would cost more in collector time than it recovers, and an invoice this small should not have been issued.", "internal_note": "Hi Leila,\n\nWe are proposing to write off the $0.37 balance on INV-112204 for Brightwater Labs under the small-balance policy. It is 96 days past due and has never been followed up.\n\nDo you want the customer told about the write-off, or should we close it without contacting them?\n\nAccounts Receivable", "case_summary": "Proposed write-off of $0.37 on INV-112204 under the $5.00 small-balance limit; 96 days past due, never touched.", "next_action": "Controller reviews and signs the write-off memo."}

## input
invoice_id: INV-108871
account: Norland Systems (ACC-1277)
billing_contact: ap@norlandsystems.example
account_owner: Diego Alvarez <diegoalvarez@ourco.example>
invoice_amount: $4.12
amount_paid: $0.00
balance_due: $4.12
status: disputed
issue_date: July 16, 2026
due_date: August 15, 2026
days_past_due: 71
last_touch: September 12, 2026 via sheet (43 days ago)
policy: write_off_max_amount $5.00, min_days_past_due 30
collector_notes (internal, never quote to the customer):
- 2026-09-12 Mei Tanaka: Customer disputes usage overage, waiting on deal desk

## output
{"memo_rationale": "The $4.12 balance on INV-108871 is under the $5.00 write-off limit and 71 days past due. The customer disputes the usage overage behind it, and resolving a dispute of this size would cost more than the balance.", "internal_note": "Hi Diego,\n\nWe are proposing to write off the $4.12 balance on INV-108871 for Norland Systems. The customer disputes the usage overage and it has been waiting on deal desk since September 12, 2026.\n\nShould deal desk close the dispute in the customer's favor once the write-off is approved?\n\nAccounts Receivable", "case_summary": "Proposed write-off of $4.12 on INV-108871, a disputed usage overage, under the $5.00 limit; 71 days past due.", "next_action": "Controller reviews the memo; deal desk closes the dispute after approval."}
