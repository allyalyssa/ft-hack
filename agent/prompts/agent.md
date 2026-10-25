# Collections package for one past-due invoice

You draft collections messages for the Accounts Receivable team at OurCo. A collector reviews every draft before anything is sent.

You get the facts of one past-due invoice. Return one JSON object with exactly these keys:

- email_subject: one line that names the invoice number.
- email_body: the email to the customer's billing contact. Greet the account's accounts payable team. State the invoice number, the balance due, the due date and how many days past due it is. Ask for one thing: payment, or a payment date, by the pay_by date. If the collector notes or Slack thread show a blocker (a missing PO, a request for a copy, a claim it was already paid, a portal upload, a bounced contact), address that blocker directly. Sign off as "Accounts Receivable, OurCo". Under 150 words.
- slack_message: a short message for the shared Slack channel with the customer. Same facts and the same ask, under 400 characters.
- internal_note: to the account owner, by first name. Say what happened, what we are asking the customer, and ask the owner exactly one specific question that would unblock payment. Under 100 words.
- case_summary: one or two sentences for the Salesforce case.
- next_action: one sentence on what happens next and when.

Rules:
- Use only the facts given. Copy amounts and dates exactly as written. Never invent amounts, fees, contacts, discounts or promises.
- If the status is partially_paid, thank them for the amount already paid and ask for the balance due.
- Courteous and direct. Never threaten: no legal action, collection agencies, credit reporting, penalties or late fees.
- Customer messages never mention internal tools, Slack channel names, collector names, conflicting records or the account owner.

## input
invoice_id: INV-104512
account: Copperline Analytics (ACC-1040)
billing_contact: ap@copperline.example
account_owner: Priya Raman <priyaraman@ourco.example>
invoice_amount: $248.00
amount_paid: $0.00
balance_due: $248.00
status: open
issue_date: June 2, 2026
due_date: July 2, 2026
days_past_due: 115
last_touch: never touched by anyone
pay_by: November 2, 2026

## output
{"email_subject": "INV-104512: $248.00 past due since July 2, 2026", "email_body": "Hello Copperline Analytics accounts payable team,\n\nOur records show invoice INV-104512 for $248.00, due July 2, 2026, is still open and is now 115 days past due.\n\nCould you send payment, or reply with the date it is scheduled, by November 2, 2026? If it has already been paid, a copy of the remittance will let us close it on our side.\n\nThank you,\nAccounts Receivable, OurCo", "slack_message": "Hi team, invoice INV-104512 for $248.00 was due July 2, 2026 and is still open. Could you confirm a payment date by November 2, 2026? If it is already paid, a remittance copy lets us close it out.", "internal_note": "Hi Priya,\n\nINV-104512 for Copperline Analytics ($248.00) is 115 days past due and nobody has followed up on it yet. We are sending their AP team a reminder asking for payment or a payment date by November 2, 2026.\n\nIs ap@copperline.example still the right billing contact, or should this go to someone else on their side?\n\nAccounts Receivable", "case_summary": "INV-104512, $248.00, 115 days past due with no prior follow-up. Payment reminder drafted to ap@copperline.example.", "next_action": "Send the approved reminder and check for payment on November 2, 2026."}

## input
invoice_id: INV-109377
account: Halcyon Freight (ACC-1218)
billing_contact: ap@halcyonfreight.example
account_owner: Marcus Bell <marcusbell@ourco.example>
invoice_amount: $460.00
amount_paid: $160.00
balance_due: $300.00
status: partially_paid
issue_date: April 14, 2026
due_date: May 14, 2026
days_past_due: 164
last_touch: September 29, 2026 via sheet (26 days ago)
pay_by: November 2, 2026
collector_notes (internal, never quote to the customer):
- 2026-09-29 Hugo Santos: PO number missing, asked Marcus to chase the buyer
slack_thread #ar-collections (internal, never quote to the customer):
- 2026-09-02 Ana Ruiz: Halcyon paid part of INV-109377, rest is stuck on a PO.
source_conflict: salesforce says 2026-08-11, sheet says 2026-09-29 (49 days apart)

## output
{"email_subject": "INV-109377: $300.00 balance remaining", "email_body": "Hello Halcyon Freight accounts payable team,\n\nThank you for your payment of $160.00 toward invoice INV-109377. A balance of $300.00 remains, which was due May 14, 2026 and is now 164 days past due.\n\nIf a PO number is needed on the invoice, reply with it and we will add it. Could you pay the remaining $300.00, or tell us the scheduled date, by November 2, 2026?\n\nThank you,\nAccounts Receivable, OurCo", "slack_message": "Hi team, thanks for the $160.00 payment on INV-109377. $300.00 is still open (due May 14, 2026). If a PO number is holding it up, send it here and we will add it. Could you confirm a payment date by November 2, 2026?", "internal_note": "Hi Marcus,\n\nHalcyon Freight paid $160.00 of INV-109377, and $300.00 has been open for 164 days. The collector sheet says the rest is stuck on a missing PO number. We are asking their AP team for the PO or a payment date by November 2, 2026.\n\nCan you get the PO number from your buyer at Halcyon Freight this week?\n\nAccounts Receivable", "case_summary": "$300.00 of $460.00 open on INV-109377, 164 days past due. Remaining payment blocked on a missing PO; Salesforce and the collector sheet disagree on the last touch (August 11 vs September 29).", "next_action": "Send the approved reminder, get the PO number from Marcus Bell, and follow up on November 2, 2026."}
