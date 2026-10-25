# customer_email

The email to the customer contact. Plain text. The first line of each shape is the subject line. The body runs 50 to 110 words. Pick the shape for the bucket in the facts. Leave out a bracketed part that starts with "If" when the facts have nothing for it.

## Shape when the bucket is agent
Invoice [invoice] for [amount] is [days] days past due

Hi [contact first name],

Invoice [invoice] for [amount] was due on [due date] and is now [days] days past due. [If a note or message shows earlier contact, a promise or a partial payment, one sentence on it.]

Could you send payment of [amount], or reply with the date you expect to pay, by [reply-by date]? If you have already paid, please reply with the payment date and reference so we can match it.

Thank you,
[sign as]

## Shape when the bucket is escalate
Your question on invoice [invoice] for [amount]

Hi [contact first name],

Invoice [invoice] for [amount] was due on [due date] and is now [days] days past due.

[Their dispute in one sentence, in their words.] We have paused reminders on this invoice while [account owner full name] reviews it.

Could you send [the one document or detail that settles it] by [reply-by date]?

Thank you,
[sign as]

## Shape when the bucket is writeoff
Balance of [amount] on invoice [invoice] is closed

Hi [contact first name],

Invoice [invoice] for [amount] was due on [due date] and is now [days] days past due.

We have closed this balance, so no payment is needed. Please do not send a payment for this invoice.

If your records show something different, reply by [reply-by date] and we will look again.

Thank you,
[sign as]

## Example 1, agent bucket
Facts: invoice INV-200118, Oakline Dental, $245.00 open, due August 12, 2026, 40 days past due, contact Sam Ortiz, collector note September 8, 2026: "Left voicemail with AP, no callback", reply by September 28, 2026, sign as Accounts Receivable.

Invoice INV-200118 for $245.00 is 40 days past due

Hi Sam,

Invoice INV-200118 for $245.00 was due on August 12, 2026 and is now 40 days past due. We left a voicemail with your AP team on September 8 and have not heard back.

Could you send payment of $245.00, or reply with the date you expect to pay, by September 28, 2026? If you have already paid, please reply with the payment date and reference so we can match it.

Thank you,
Accounts Receivable

## Example 2, escalate bucket
Facts: invoice INV-201734, Grayson Print Works, $188.50 open, disputed, due July 25, 2026, 58 days past due, contact Lena Park, Slack September 10, 2026: "We were billed for 500 brochures and received 400", account owner Tom Reyes, reply by September 28, 2026, sign as Accounts Receivable.

Your question on invoice INV-201734 for $188.50

Hi Lena,

Invoice INV-201734 for $188.50 was due on July 25, 2026 and is now 58 days past due.

You told us you were billed for 500 brochures and received 400. We have paused reminders on this invoice while Tom Reyes reviews it.

Could you send the delivery receipt or packing slip for this order by September 28, 2026?

Thank you,
Accounts Receivable
