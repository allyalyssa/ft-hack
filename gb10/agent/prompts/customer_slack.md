# customer_slack

One Slack message to the customer contact in our shared channel. One paragraph, under 600 characters, no sign-off. Pick the shape for the bucket in the facts. Leave out a bracketed part that starts with "If" when the facts have nothing for it.

## Shape when the bucket is agent
Hi [contact first name], invoice [invoice] for [amount] was due [due date] and is now [days] days past due. [If a note or message shows earlier contact, a promise or a partial payment, one sentence on it.] Could you send payment or a pay date by [reply-by date]? If it is already paid, the payment date and reference help us match it.

## Shape when the bucket is escalate
Hi [contact first name], thanks for flagging the issue on invoice [invoice] for [amount] (due [due date], [days] days past due). We have paused reminders while [account owner full name] reviews it. Could you send [the one document or detail that settles it] by [reply-by date]?

## Shape when the bucket is writeoff
Hi [contact first name], we have closed the [amount] balance on invoice [invoice] (due [due date], [days] days past due), so no payment is needed. Please do not send one for this invoice. If your records differ, let us know by [reply-by date].

## Example 1, agent bucket
Facts: invoice INV-200553, Pinecrest Veterinary, $96.40 open, due August 19, 2026, 33 days past due, contact Ana Gomez, collector note September 3, 2026: "Ana says the invoice is with their AP team", reply by September 28, 2026.

Hi Ana, invoice INV-200553 for $96.40 was due August 19, 2026 and is now 33 days past due. Thanks for letting us know on September 3 that it is with your AP team. Could you send payment or a pay date by September 28, 2026? If it is already paid, the payment date and reference help us match it.

## Example 2, writeoff bucket
Facts: invoice INV-203310, Harbor Lane Bakery, $1.12 open, due May 27, 2026, 117 days past due, contact Rob Klein, no touch on record, reply by September 28, 2026.

Hi Rob, we have closed the $1.12 balance on invoice INV-203310 (due May 27, 2026, 117 days past due), so no payment is needed. Please do not send one for this invoice. If your records differ, let us know by September 28, 2026.
